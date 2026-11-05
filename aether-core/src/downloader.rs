use anyhow::{Context, Result};
use futures_util::StreamExt;
use reqwest::header::{HeaderMap, ACCEPT_RANGES, CONTENT_LENGTH, CONTENT_RANGE, RANGE, USER_AGENT};
use std::fs::OpenOptions;
use std::io::SeekFrom;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};
use tokio::io::AsyncSeekExt;

pub struct Metadata {
    pub total_size: u64,
    pub supports_range: bool,
    pub filename: String,
}

pub struct Downloader {
    url: String,
    output_dir: PathBuf,
    connections: usize,
    client: reqwest::Client,
}

impl Downloader {
    pub fn new(url: String, output_dir: PathBuf, connections: usize) -> Self {
        let client = reqwest::Client::builder()
            .timeout(Duration::from_secs(30))
            .build()
            .unwrap_or_default();

        Self {
            url,
            output_dir,
            connections: connections.clamp(1, 32),
            client,
        }
    }

    pub async fn probe(&self) -> Result<Metadata> {
        let mut headers = HeaderMap::new();
        headers.insert(USER_AGENT, "Aether/1.0 (Rust Core Engine)".parse()?);

        // 1. Probar HEAD
        let head_resp = self.client.head(&self.url).headers(headers.clone()).send().await;
        
        let mut total_size = 0u64;
        let mut supports_range = false;
        let mut filename = String::from("download.bin");

        if let Ok(resp) = head_resp {
            if resp.status().is_success() {
                if let Some(cl) = resp.headers().get(CONTENT_LENGTH) {
                    if let Ok(s) = cl.to_str() {
                        total_size = s.parse().unwrap_or(0);
                    }
                }
                if let Some(ar) = resp.headers().get(ACCEPT_RANGES) {
                    if let Ok(s) = ar.to_str() {
                        supports_range = s.to_lowercase() == "bytes";
                    }
                }
            }
        }

        // 2. Si no hay tamaño o no confirmó range, probar GET de 1 byte
        if total_size == 0 || !supports_range {
            headers.insert(RANGE, "bytes=0-0".parse()?);
            if let Ok(resp) = self.client.get(&self.url).headers(headers).send().await {
                if resp.status().as_u16() == 206 {
                    supports_range = true;
                    if let Some(cr) = resp.headers().get(CONTENT_RANGE) {
                        if let Ok(s) = cr.to_str() {
                            if let Some(slash_pos) = s.rfind('/') {
                                total_size = s[slash_pos + 1..].trim().parse().unwrap_or(0);
                            }
                        }
                    }
                }
            }
        }

        // Deducción de nombre desde la URL
        if let Ok(parsed) = reqwest::Url::parse(&self.url) {
            if let Some(segments) = parsed.path_segments() {
                if let Some(last) = segments.last() {
                    if !last.is_empty() {
                        filename = last.to_string();
                    }
                }
            }
        }

        Ok(Metadata {
            total_size,
            supports_range,
            filename,
        })
    }

    pub async fn download(&self) -> Result<PathBuf> {
        let meta = self.probe().await.context("Error al sondear la URL")?;
        tokio::fs::create_dir_all(&self.output_dir).await?;

        let output_path = self.output_dir.join(&meta.filename);
        println!("\n⚡ \x1b[1;36mAETHER RUST CORE\x1b[0m — Concurrencia Pura");
        println!("📁 Archivo: \x1b[1;32m{}\x1b[0m", meta.filename);
        println!("📊 Tamaño:  \x1b[1;33m{:.2} MB\x1b[0m", meta.total_size as f64 / (1024.0 * 1024.0));
        println!("🧵 Hilos:   \x1b[1;35m{}\x1b[0m (Range: {})", self.connections, meta.supports_range);

        // Pre-asignar archivo en disco
        {
            let std_file = OpenOptions::new()
                .write(true)
                .create(true)
                .truncate(true)
                .open(&output_path)?;
            if meta.total_size > 0 {
                std_file.set_len(meta.total_size)?;
            }
        }

        let downloaded_bytes = Arc::new(AtomicU64::new(0));
        let is_running = Arc::new(AtomicBool::new(true));

        if meta.supports_range && meta.total_size > 0 && self.connections > 1 {
            let chunk_size = meta.total_size / self.connections as u64;
            let mut handles = Vec::new();

            for i in 0..self.connections {
                let start = i as u64 * chunk_size;
                let end = if i == self.connections - 1 {
                    meta.total_size - 1
                } else {
                    (i as u64 + 1) * chunk_size - 1
                };

                let url = self.url.clone();
                let path = output_path.clone();
                let client = self.client.clone();
                let counter = Arc::clone(&downloaded_bytes);
                let running = Arc::clone(&is_running);

                let handle = tokio::spawn(async move {
                    Self::worker(i, url, path, start, end, client, counter, running).await
                });
                handles.push(handle);
            }

            // Monitor de telemetría en tiempo real
            let monitor_counter = Arc::clone(&downloaded_bytes);
            let total = meta.total_size;
            let monitor_running = Arc::clone(&is_running);

            let monitor_handle = tokio::spawn(async move {
                let mut last_bytes = 0u64;
                let mut last_time = Instant::now();

                while monitor_running.load(Ordering::Relaxed) {
                    tokio::time::sleep(Duration::from_millis(500)).await;
                    let current = monitor_counter.load(Ordering::Relaxed);
                    let now = Instant::now();
                    let elapsed = (now - last_time).as_secs_f64();

                    if elapsed > 0.0 {
                        let speed_bps = (current.saturating_sub(last_bytes)) as f64 / elapsed;
                        let speed_mb = speed_bps / (1024.0 * 1024.0);
                        let pct = if total > 0 { (current as f64 / total as f64) * 100.0 } else { 0.0 };

                        print!("\r🚀 Velocidad: \x1b[1;32m{:.2} MB/s\x1b[0m | Progreso: \x1b[1;36m{:.1}%\x1b[0m ({:.1}/{:.1} MB)    ",
                            speed_mb, pct, current as f64 / 1_048_576.0, total as f64 / 1_048_576.0);
                        let _ = std::io::Write::flush(&mut std::io::stdout());

                        last_bytes = current;
                        last_time = now;
                    }
                }
            });

            for h in handles {
                h.await??;
            }

            is_running.store(false, Ordering::Relaxed);
            let _ = monitor_handle.await;
        } else {
            // Descarga lineal
            Self::single_stream_download(&self.url, &output_path, &self.client, Arc::clone(&downloaded_bytes)).await?;
        }

        println!("\n\x1b[1;32m✔ Descarga completada con éxito en:\x1b[0m {:?}", output_path);
        Ok(output_path)
    }

    async fn worker(
        _id: usize,
        url: String,
        output_path: PathBuf,
        start: u64,
        end: u64,
        client: reqwest::Client,
        counter: Arc<AtomicU64>,
        running: Arc<AtomicBool>,
    ) -> Result<()> {
        let mut file = tokio::fs::OpenOptions::new()
            .write(true)
            .open(&output_path)
            .await?;

        file.seek(SeekFrom::Start(start)).await?;

        let range_val = format!("bytes={}-{}", start, end);
        let resp = client
            .get(&url)
            .header(RANGE, range_val)
            .send()
            .await?
            .error_for_status()?;

        let mut stream = resp.bytes_stream();
        while running.load(Ordering::Relaxed) {
            if let Some(chunk_res) = stream.next().await {
                let chunk = chunk_res?;
                tokio::io::AsyncWriteExt::write_all(&mut file, &chunk).await?;
                counter.fetch_add(chunk.len() as u64, Ordering::Relaxed);
            } else {
                break;
            }
        }

        Ok(())
    }

    async fn single_stream_download(
        url: &str,
        output_path: &Path,
        client: &reqwest::Client,
        counter: Arc<AtomicU64>,
    ) -> Result<()> {
        let resp = client.get(url).send().await?.error_for_status()?;
        let mut file = tokio::fs::File::create(output_path).await?;
        let mut stream = resp.bytes_stream();

        while let Some(chunk_res) = stream.next().await {
            let chunk = chunk_res?;
            tokio::io::AsyncWriteExt::write_all(&mut file, &chunk).await?;
            counter.fetch_add(chunk.len() as u64, Ordering::Relaxed);
        }
        Ok(())
    }
}
