#![allow(dead_code)]

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub enum SegmentStatus {
    Pending,
    Downloading,
    Completed,
    Paused,
    Error,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Segment {
    pub id: usize,
    pub start: u64,
    pub current: u64,
    pub end: u64,
    pub status: SegmentStatus,
}

impl Segment {
    pub fn new(id: usize, start: u64, end: u64) -> Self {
        Self {
            id,
            start,
            current: start,
            end,
            status: SegmentStatus::Pending,
        }
    }

    pub fn total_bytes(&self) -> u64 {
        if self.end >= self.start {
            self.end - self.start + 1
        } else {
            0
        }
    }

    pub fn remaining_bytes(&self) -> u64 {
        if self.end >= self.current {
            self.end - self.current + 1
        } else {
            0
        }
    }

    pub fn progress_pct(&self) -> f64 {
        let total = self.total_bytes() as f64;
        if total <= 0.0 {
            return 100.0;
        }
        let done = (self.current - self.start) as f64;
        (done / total * 100.0).clamp(0.0, 100.0)
    }
}
