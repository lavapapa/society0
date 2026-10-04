//! 隔离试验：数据由 Reader 生成，搜索与跨块缓冲完全交给 ripgrep。
use grep_regex::RegexMatcherBuilder;
use grep_searcher::{Searcher, SearcherBuilder, Sink, SinkMatch};
use std::{alloc::{GlobalAlloc, Layout, System}, io::{self, Read}, sync::atomic::{AtomicUsize, Ordering::Relaxed}, time::Instant};

struct Meter;
static LIVE: AtomicUsize = AtomicUsize::new(0);
static PEAK: AtomicUsize = AtomicUsize::new(0);
static MAX_REQUEST: AtomicUsize = AtomicUsize::new(0);
fn allocated(n: usize) {
    let live = LIVE.fetch_add(n, Relaxed) + n;
    PEAK.fetch_max(live, Relaxed);
    MAX_REQUEST.fetch_max(n, Relaxed);
}
unsafe impl GlobalAlloc for Meter {
    unsafe fn alloc(&self, l: Layout) -> *mut u8 {
        let p = unsafe { System.alloc(l) };
        if !p.is_null() { allocated(l.size()); }
        p
    }
    unsafe fn dealloc(&self, p: *mut u8, l: Layout) {
        LIVE.fetch_sub(l.size(), Relaxed);
        unsafe { System.dealloc(p, l); }
    }
    unsafe fn realloc(&self, p: *mut u8, l: Layout, n: usize) -> *mut u8 {
        let q = unsafe { System.realloc(p, l, n) };
        if !q.is_null() {
            LIVE.fetch_sub(l.size(), Relaxed);
            allocated(n);
        }
        q
    }
}
#[global_allocator]
static ALLOCATOR: Meter = Meter;

// 测试数据生成器：每行指定位置放中文词，其他字节为 x；无完整正文分配。
struct Generated {
    length: usize, line_length: usize, marker_at: usize, chunk: usize,
    offset: usize, calls: usize, max_returned: usize, max_requested: usize,
}
impl Read for Generated {
    fn read(&mut self, out: &mut [u8]) -> io::Result<usize> {
        self.calls += 1;
        self.max_requested = self.max_requested.max(out.len());
        let n = out.len().min(self.chunk).min(self.length - self.offset);
        for (i, byte) in out[..n].iter_mut().enumerate() {
            let p = (self.offset + i) % self.line_length;
            *byte = if p == self.line_length - 1 { b'\n' }
                else if p >= self.marker_at && p < self.marker_at + "关键词".len() {
                    "关键词".as_bytes()[p - self.marker_at]
                } else { b'x' };
        }
        self.offset += n;
        self.max_returned = self.max_returned.max(n);
        Ok(n)
    }
}
#[derive(Default)]
struct Hits { count: usize, max_line: usize, first_offset: Option<u64>, first_line: Option<u64>, stop: bool }
impl Sink for &mut Hits {
    type Error = io::Error;
    fn matched(&mut self, _: &Searcher, m: &SinkMatch<'_>) -> io::Result<bool> {
        self.count += 1;
        self.max_line = self.max_line.max(m.bytes().len());
        self.first_offset.get_or_insert(m.absolute_byte_offset());
        self.first_line = self.first_line.or(m.line_number());
        Ok(!self.stop)
    }
}
fn run(name: &str, length: usize, line_length: usize, marker_at: usize, chunk: usize, limit: Option<usize>, stop: bool) {
    let matcher = RegexMatcherBuilder::new().line_terminator(Some(b'\n')).build("关(?:键)词").unwrap();
    let mut reader = Generated { length, line_length, marker_at, chunk, offset: 0, calls: 0, max_returned: 0, max_requested: 0 };
    let baseline = LIVE.load(Relaxed);
    PEAK.store(baseline, Relaxed);
    MAX_REQUEST.store(0, Relaxed);
    let started = Instant::now();
    let mut searcher = SearcherBuilder::new().bom_sniffing(false).multi_line(false).heap_limit(limit).build();
    let mut hits = Hits { stop, ..Hits::default() };
    let result = searcher.search_reader(&matcher, &mut reader, &mut hits);
    let micros = started.elapsed().as_micros();
    let peak = PEAK.load(Relaxed) - baseline;
    let largest = MAX_REQUEST.load(Relaxed);
    println!("{{\"case\":\"{name}\",\"bytes_read\":{},\"read_calls\":{},\"max_read_returned\":{},\"max_read_requested\":{},\"hits\":{},\"max_matched_line\":{},\"peak_heap_delta\":{},\"largest_allocation_request\":{},\"elapsed_us\":{},\"error\":{:?}}}", reader.offset, reader.calls, reader.max_returned, reader.max_requested, hits.count, hits.max_line, peak, largest, micros, result.as_ref().err().map(|e|e.to_string()).unwrap_or_default());
    if limit == Some(65536) && line_length > 65536 { assert!(result.is_err()); }
    else {
        result.unwrap();
        assert_eq!(hits.count, if stop {1} else { length / line_length });
        assert_eq!(hits.first_offset, Some(0));
        assert_eq!(hits.first_line, Some(1));
        if stop { assert!(reader.offset < length); }
    }
}
fn main() {
    run("utf8_split_every_two_bytes", 32, 32, 7, 2, None, false);
    run("keyword_across_64k_boundary", 131072, 131072, 65533, 65536, None, false);
    run("one_mib_single_line", 1048576, 1048576, 65533, 65536, None, false);
    run("four_one_mib_lines", 4194304, 1048576, 65533, 65536, None, false);
    run("four_mib_short_lines", 4194304, 128, 60, 65536, None, false);
    run("one_mib_line_64k_heap_limit", 1048576, 1048576, 65533, 65536, Some(65536), false);
    run("stop_after_first_line", 4194304, 128, 60, 65536, None, true);
    let matcher = RegexMatcherBuilder::new().line_terminator(Some(b'\n')).build("关键词").unwrap();
    let mut searcher = SearcherBuilder::new().bom_sniffing(false).build();
    let mut counts = Vec::new();
    for doc in ["关键", "词", "关键词"] {
        let mut hits = Hits::default();
        searcher.search_reader(&matcher, doc.as_bytes(), &mut hits).unwrap();
        counts.push(hits.count);
    }
    assert_eq!(counts, [0, 0, 1]);
    println!("{{\"case\":\"logical_documents_separate\",\"hits\":{counts:?}}}");
    assert!(RegexMatcherBuilder::new().line_terminator(Some(b'\n')).build("关键\\n词").is_err());
    println!("{{\"case\":\"explicit_newline_regex_rejected\",\"passed\":true}}");
    let began = Instant::now();
    for _ in 0..1000 {
        let mut hits = Hits::default();
        searcher.search_reader(&matcher, "xxxxxxx关键词\n".as_bytes(), &mut hits).unwrap();
        assert_eq!(hits.count, 1);
    }
    println!("{{\"case\":\"tiny_document_in_process\",\"n\":1000,\"mean_us\":{}}}", began.elapsed().as_secs_f64() * 1000.0);
}
