mod downloader;
mod segment;

use clap::Parser;
use std::path::PathBuf;

#[derive(Parser, Debug)]
#[command(name = "aether-core")]
#[command(about = "Aether High-Speed Download Accelerator — Pure Rust Engine", long_about = None)]
struct Args {
    /// URL directa del archivo a descargar
    #[arg(required = true)]
    url: String,

    /// Número de conexiones concurrentes paralelas (1 a 32)
    #[arg(short = 'c', long = "connections", default_value_t = 8)]
    connections: usize,

    /// Directorio de destino para la descarga
    #[arg(short = 'o', long = "output")]
    output: Option<PathBuf>,
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let args = Args::parse();

    let output_dir = args.output.unwrap_or_else(|| {
        let home = std::env::var("HOME").unwrap_or_else(|_| ".".to_string());
        let descargas = PathBuf::from(&home).join("Descargas").join("Aether");
        if descargas.parent().map(|p| p.exists()).unwrap_or(false) {
            descargas
        } else {
            PathBuf::from(&home).join("Downloads").join("Aether")
        }
    });

    let dl = downloader::Downloader::new(args.url, output_dir, args.connections);
    dl.download().await?;

    Ok(())
}
