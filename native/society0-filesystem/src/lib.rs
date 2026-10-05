//! 文件系统桥：原生 OverlayFs 负责文件语义，适配器导出变更与删除路径。
use async_trait::async_trait;
use bashkit::interop::fs::{BashkitFsAbiOwnedHandleV1, export_filesystem, import_owned_filesystem};
use bashkit::{DirEntry, FileSystem, FileSystemExt, Metadata, OverlayFs, Result, VfsEntryKind};
use pyo3::{
    prelude::*,
    types::{PyBytes, PyCapsule, PyDict, PyList},
};
use std::{
    path::{Path, PathBuf},
    sync::{
        Arc, Mutex,
        atomic::{AtomicU64, Ordering},
    },
    time::SystemTime,
};

struct DeltaFs {
    inner: OverlayFs,
    removed: Mutex<Vec<PathBuf>>,
    range_cache: Mutex<Option<(PathBuf, Vec<u8>)>>,
    revision: AtomicU64,
}
#[async_trait]
impl FileSystemExt for DeltaFs {}
#[async_trait]
impl FileSystem for DeltaFs {
    async fn read_file(&self, p: &Path) -> Result<Vec<u8>> {
        self.inner.read_file(p).await
    }
    async fn write_file(&self, p: &Path, c: &[u8]) -> Result<()> {
        *self.range_cache.lock().unwrap() = None;
        self.revision.fetch_add(1, Ordering::Relaxed);
        self.inner.write_file(p, c).await
    }
    async fn append_file(&self, p: &Path, c: &[u8]) -> Result<()> {
        *self.range_cache.lock().unwrap() = None;
        self.revision.fetch_add(1, Ordering::Relaxed);
        self.inner.append_file(p, c).await
    }
    async fn mkdir(&self, p: &Path, r: bool) -> Result<()> {
        *self.range_cache.lock().unwrap() = None;
        self.revision.fetch_add(1, Ordering::Relaxed);
        self.inner.mkdir(p, r).await
    }
    async fn remove(&self, p: &Path, r: bool) -> Result<()> {
        *self.range_cache.lock().unwrap() = None;
        self.revision.fetch_add(1, Ordering::Relaxed);
        self.inner.remove(p, r).await?;
        self.removed.lock().unwrap().push(p.into());
        Ok(())
    }
    async fn stat(&self, p: &Path) -> Result<Metadata> {
        self.inner.stat(p).await
    }
    async fn read_dir(&self, p: &Path) -> Result<Vec<DirEntry>> {
        self.inner.read_dir(p).await
    }
    async fn exists(&self, p: &Path) -> Result<bool> {
        self.inner.exists(p).await
    }
    async fn rename(&self, a: &Path, b: &Path) -> Result<()> {
        *self.range_cache.lock().unwrap() = None;
        self.revision.fetch_add(1, Ordering::Relaxed);
        self.inner.rename(a, b).await?;
        self.removed.lock().unwrap().push(a.into());
        Ok(())
    }
    async fn copy(&self, a: &Path, b: &Path) -> Result<()> {
        *self.range_cache.lock().unwrap() = None;
        self.revision.fetch_add(1, Ordering::Relaxed);
        self.inner.copy(a, b).await
    }
    async fn symlink(&self, a: &Path, b: &Path) -> Result<()> {
        *self.range_cache.lock().unwrap() = None;
        self.revision.fetch_add(1, Ordering::Relaxed);
        self.inner.symlink(a, b).await
    }
    async fn read_link(&self, p: &Path) -> Result<PathBuf> {
        self.inner.read_link(p).await
    }
    async fn chmod(&self, p: &Path, m: u32) -> Result<()> {
        *self.range_cache.lock().unwrap() = None;
        self.revision.fetch_add(1, Ordering::Relaxed);
        self.inner.chmod(p, m).await
    }
    async fn set_modified_time(&self, p: &Path, t: SystemTime) -> Result<()> {
        *self.range_cache.lock().unwrap() = None;
        self.revision.fetch_add(1, Ordering::Relaxed);
        self.inner.set_modified_time(p, t).await
    }
}
#[pyclass]
struct Overlay {
    fs: Arc<DeltaFs>,
}
#[pymethods]
impl Overlay {
    #[new]
    #[pyo3(signature=(lower, root_mode=493, root_modified_ns=0))]
    fn new(lower: Bound<'_, PyCapsule>, root_mode: u32, root_modified_ns: i64) -> PyResult<Self> {
        let pointer = lower.pointer_checked(Some(c"bashkit.FileSystem.v1"))?;
        let handle = unsafe { &*pointer.as_ptr().cast::<BashkitFsAbiOwnedHandleV1>() };
        let lower = unsafe { import_owned_filesystem(handle) }
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;
        let inner = OverlayFs::new(lower);
        inner
            .upper()
            .restore(&bashkit::VfsSnapshot::from_entries(vec![
                bashkit::VfsEntry {
                    path: PathBuf::from("/"),
                    kind: VfsEntryKind::Directory,
                    mode: root_mode,
                },
            ]))
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;
        pyo3_async_runtimes::tokio::get_runtime()
            .block_on(
                inner
                    .upper()
                    .set_modified_time(Path::new("/"), system_time(root_modified_ns)),
            )
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;
        Ok(Self {
            fs: Arc::new(DeltaFs {
                inner,
                removed: Mutex::new(Vec::new()),
                range_cache: Mutex::new(None),
                revision: AtomicU64::new(0),
            }),
        })
    }
    fn capsule<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyCapsule>> {
        let handle = export_filesystem(self.fs.clone())
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;
        PyCapsule::new_with_value(py, handle, c"bashkit.FileSystem.v1")
    }
    fn revision(&self) -> u64 {
        self.fs.revision.load(Ordering::Relaxed)
    }
    fn exists<'py>(&self, py: Python<'py>, path: String) -> PyResult<Bound<'py, PyAny>> {
        let fs = self.fs.clone();
        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            fs.inner.exists(Path::new(&path)).await.map_err(py_fs_error)
        })
    }
    fn upper_exists<'py>(&self, py: Python<'py>, path: String) -> PyResult<Bound<'py, PyAny>> {
        let fs = self.fs.clone();
        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            fs.inner
                .upper()
                .exists(Path::new(&path))
                .await
                .map_err(py_fs_error)
        })
    }
    fn read_range<'py>(
        &self,
        py: Python<'py>,
        path: String,
        offset: usize,
        size: usize,
    ) -> PyResult<Bound<'py, PyAny>> {
        let fs = self.fs.clone();
        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            let cached = {
                let cache = fs.range_cache.lock().unwrap();
                cache
                    .as_ref()
                    .filter(|(p, _)| p == Path::new(&path))
                    .map(|(_, data)| {
                        (
                            data[offset.min(data.len())
                                ..offset.saturating_add(size).min(data.len())]
                                .to_vec(),
                            data.len(),
                        )
                    })
            };
            let (data, total) = if let Some(value) = cached {
                value
            } else {
                // Bashkit 上层公共接口整读；单槽复用同版正文，连续范围不重复复制整文。
                let body = fs
                    .inner
                    .read_file(Path::new(&path))
                    .await
                    .map_err(py_fs_error)?;
                let total = body.len();
                let data = body[offset.min(total)..offset.saturating_add(size).min(total)].to_vec();
                *fs.range_cache.lock().unwrap() = Some((PathBuf::from(path), body));
                (data, total)
            };
            Python::attach(|py| Ok((PyBytes::new(py, &data).unbind(), total)))
        })
    }
    fn stat<'py>(&self, py: Python<'py>, path: String) -> PyResult<Bound<'py, PyAny>> {
        let fs = self.fs.clone();
        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            let meta = fs.inner.stat(Path::new(&path)).await.map_err(py_fs_error)?;
            Ok((
                if meta.file_type == bashkit::FileType::Directory {
                    "directory"
                } else {
                    "file"
                },
                meta.size,
                meta.mode,
                nanos(meta.modified),
                nanos(meta.created),
            ))
        })
    }
    fn list<'py>(&self, py: Python<'py>, path: String) -> PyResult<Bound<'py, PyAny>> {
        let fs = self.fs.clone();
        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            let rows = fs
                .inner
                .read_dir(Path::new(&path))
                .await
                .map_err(py_fs_error)?;
            Ok(rows
                .into_iter()
                .map(|row| {
                    (
                        row.name,
                        if row.metadata.file_type == bashkit::FileType::Directory {
                            "directory"
                        } else {
                            "file"
                        },
                        row.metadata.size,
                    )
                })
                .collect::<Vec<_>>())
        })
    }
    fn changes<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let result = PyDict::new(py);
        let entries = PyList::empty(py);
        for entry in self.fs.inner.upper().snapshot().entries() {
            let item = PyDict::new(py);
            item.set_item("path", entry.path.to_string_lossy().as_ref())?;
            item.set_item("mode", entry.mode)?;
            let meta = pyo3_async_runtimes::tokio::get_runtime()
                .block_on(self.fs.inner.upper().stat(&entry.path))
                .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;
            item.set_item("modified_ns", nanos(meta.modified))?;
            item.set_item("created_ns", nanos(meta.created))?;
            match &entry.kind {
                VfsEntryKind::File { content } => {
                    item.set_item("kind", "file")?;
                    item.set_item("content", PyBytes::new(py, content))?;
                }
                VfsEntryKind::Directory => {
                    item.set_item("kind", "directory")?;
                }
                VfsEntryKind::Symlink { target } => {
                    item.set_item("kind", "symlink")?;
                    item.set_item("target", target.to_string_lossy().as_ref())?;
                }
                VfsEntryKind::Fifo => {
                    item.set_item("kind", "fifo")?;
                }
            }
            entries.append(item)?;
        }
        result.set_item("entries", entries)?;
        result.set_item(
            "removed",
            self.fs
                .removed
                .lock()
                .unwrap()
                .iter()
                .map(|p| p.to_string_lossy().into_owned())
                .collect::<Vec<_>>(),
        )?;
        Ok(result)
    }
}
#[pymodule]
fn society0_filesystem(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<Overlay>()?;
    m.add_function(wrap_pyfunction!(search_reader, m)?)?;
    m.add_function(wrap_pyfunction!(callback_filesystem, m)?)?;
    Ok(())
}

// 只读下层仅桥接到调用者事件循环，目录和正文由成熟数据源提供。
struct CallbackFs {
    callback: Py<PyAny>,
    locals: pyo3_async_runtimes::TaskLocals,
}
fn py_fs_error(error: bashkit::Error) -> PyErr {
    if let bashkit::Error::Io(ref value) = error {
        match value.kind() {
            std::io::ErrorKind::NotFound => {
                return pyo3::exceptions::PyFileNotFoundError::new_err(error.to_string());
            }
            std::io::ErrorKind::NotADirectory => {
                return pyo3::exceptions::PyNotADirectoryError::new_err(error.to_string());
            }
            std::io::ErrorKind::IsADirectory => {
                return pyo3::exceptions::PyIsADirectoryError::new_err(error.to_string());
            }
            _ => (),
        }
    }
    pyo3::exceptions::PyRuntimeError::new_err(error.to_string())
}
fn io_error(e: PyErr) -> bashkit::Error {
    std::io::Error::other(e.to_string()).into()
}
fn readonly() -> bashkit::Error {
    std::io::Error::new(std::io::ErrorKind::PermissionDenied, "read-only filesystem").into()
}
fn nanos(time: SystemTime) -> i128 {
    match time.duration_since(std::time::UNIX_EPOCH) {
        Ok(value) => value.as_nanos() as i128,
        Err(error) => -(error.duration().as_nanos() as i128),
    }
}
fn system_time(value: i64) -> SystemTime {
    let duration = std::time::Duration::from_nanos(value.unsigned_abs());
    if value >= 0 {
        std::time::UNIX_EPOCH + duration
    } else {
        std::time::UNIX_EPOCH - duration
    }
}
fn metadata(value: (String, u64, u32, i64, i64)) -> Metadata {
    Metadata {
        file_type: match value.0.as_str() {
            "directory" => bashkit::FileType::Directory,
            "symlink" => bashkit::FileType::Symlink,
            _ => bashkit::FileType::File,
        },
        size: value.1,
        mode: value.2,
        modified: system_time(value.3),
        created: system_time(value.4),
    }
}
impl CallbackFs {
    async fn call(&self, op: &str, path: &Path) -> Result<Py<PyAny>> {
        let future = Python::attach(|py| {
            let awaitable = self
                .callback
                .bind(py)
                .call1((op, path.to_string_lossy().as_ref()))?;
            pyo3_async_runtimes::into_future_with_locals(&self.locals, awaitable)
        })
        .map_err(io_error)?;
        future.await.map_err(io_error)
    }
}
#[async_trait]
impl FileSystemExt for CallbackFs {}
#[async_trait]
impl FileSystem for CallbackFs {
    async fn read_file(&self, p: &Path) -> Result<Vec<u8>> {
        let value = self.call("read", p).await?;
        Python::attach(|py| value.extract(py)).map_err(io_error)
    }
    async fn stat(&self, p: &Path) -> Result<Metadata> {
        let value = self.call("stat", p).await?;
        Python::attach(|py| value.extract(py))
            .map(metadata)
            .map_err(io_error)
    }
    async fn read_dir(&self, p: &Path) -> Result<Vec<DirEntry>> {
        let value = self.call("list", p).await?;
        let rows: Vec<(String, String, u64, u32, i64, i64)> =
            Python::attach(|py| value.extract(py)).map_err(io_error)?;
        Ok(rows
            .into_iter()
            .map(|(name, d, s, m, modified, created)| DirEntry {
                name,
                metadata: metadata((d, s, m, modified, created)),
            })
            .collect())
    }
    async fn exists(&self, p: &Path) -> Result<bool> {
        let value = self.call("exists", p).await?;
        Python::attach(|py| value.extract(py)).map_err(io_error)
    }
    async fn read_link(&self, p: &Path) -> Result<PathBuf> {
        let value = self.call("read_link", p).await?;
        let path: String = Python::attach(|py| value.extract(py)).map_err(io_error)?;
        Ok(path.into())
    }
    async fn write_file(&self, _: &Path, _: &[u8]) -> Result<()> {
        Err(readonly())
    }
    async fn append_file(&self, _: &Path, _: &[u8]) -> Result<()> {
        Err(readonly())
    }
    async fn mkdir(&self, _: &Path, _: bool) -> Result<()> {
        Err(readonly())
    }
    async fn remove(&self, _: &Path, _: bool) -> Result<()> {
        Err(readonly())
    }
    async fn rename(&self, _: &Path, _: &Path) -> Result<()> {
        Err(readonly())
    }
    async fn copy(&self, _: &Path, _: &Path) -> Result<()> {
        Err(readonly())
    }
    async fn symlink(&self, _: &Path, _: &Path) -> Result<()> {
        Err(readonly())
    }
    async fn chmod(&self, _: &Path, _: u32) -> Result<()> {
        Err(readonly())
    }
    async fn set_modified_time(&self, _: &Path, _: SystemTime) -> Result<()> {
        Err(readonly())
    }
}
#[pyfunction]
fn callback_filesystem<'py>(
    py: Python<'py>,
    callback: Py<PyAny>,
) -> PyResult<Bound<'py, PyCapsule>> {
    let locals = pyo3_async_runtimes::tokio::get_current_locals(py)?;
    let handle = export_filesystem(Arc::new(CallbackFs { callback, locals }))
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;
    PyCapsule::new_with_value(py, handle, c"bashkit.FileSystem.v1")
}

// 一个逻辑文件对应一个连续 Reader；块边界与逐行正则交给 ripgrep。
struct SearchCallbacks {
    read: Py<PyAny>,
    sink: Py<PyAny>,
    locals: pyo3_async_runtimes::TaskLocals,
    offset: u64,
}
impl SearchCallbacks {
    fn wait(
        &self,
        callback: &Py<PyAny>,
        args: impl for<'py> pyo3::call::PyCallArgs<'py>,
    ) -> std::io::Result<Py<PyAny>> {
        let future = Python::attach(|py| {
            let awaitable = callback.bind(py).call1(args)?;
            pyo3_async_runtimes::into_future_with_locals(&self.locals, awaitable)
        })
        .map_err(|e: PyErr| std::io::Error::other(e.to_string()))?;
        pyo3_async_runtimes::tokio::get_runtime()
            .block_on(future)
            .map_err(|e| std::io::Error::other(e.to_string()))
    }
}
impl std::io::Read for SearchCallbacks {
    fn read(&mut self, out: &mut [u8]) -> std::io::Result<usize> {
        if out.is_empty() {
            return Ok(0);
        }
        let size = out.len().min(65536);
        let value = self.wait(&self.read, (self.offset, size))?;
        let data: Vec<u8> = Python::attach(|py| value.extract::<Vec<u8>>(py))
            .map_err(|e| std::io::Error::other(e.to_string()))?;
        if data.len() > size {
            return Err(std::io::Error::other(
                "range callback exceeded requested size",
            ));
        }
        out[..data.len()].copy_from_slice(&data);
        self.offset += data.len() as u64;
        Ok(data.len())
    }
}
struct SearchSink<'a> {
    callbacks: &'a SearchCallbacks,
    matches: &'a mut u64,
    batches: &'a mut u64,
    pending: Vec<(Option<u64>, u64, Vec<u8>)>,
    pending_bytes: usize,
}
impl SearchSink<'_> {
    fn flush(&mut self) -> std::io::Result<()> {
        if self.pending.is_empty() { return Ok(()); }
        let batch = Python::attach(|py| {
            let items = PyList::empty(py);
            for (line, offset, body) in self.pending.drain(..) {
                items.append((line, offset, PyBytes::new(py, &body)))?;
            }
            Ok::<_, PyErr>(items.unbind())
        }).map_err(|e| std::io::Error::other(e.to_string()))?;
        self.callbacks.wait(&self.callbacks.sink, (batch,))?;
        *self.batches += 1;
        self.pending_bytes = 0;
        Ok(())
    }
}
impl grep_searcher::Sink for SearchSink<'_> {
    type Error = std::io::Error;
    fn matched(&mut self, _: &grep_searcher::Searcher, hit: &grep_searcher::SinkMatch<'_>) -> std::io::Result<bool> {
        // 传输批次约 64 KiB；超长单行保留原文并单独承担其内存成本。
        self.pending_bytes += hit.bytes().len() + 32;
        self.pending.push((hit.line_number(), hit.absolute_byte_offset(), hit.bytes().to_vec()));
        *self.matches += 1;
        if self.pending_bytes >= 65536 { self.flush()?; }
        Ok(true)
    }
    fn finish(&mut self, _: &grep_searcher::Searcher, _: &grep_searcher::SinkFinish) -> std::io::Result<()> {
        self.flush()
    }
}
#[pyfunction]
#[pyo3(signature=(read, sink, pattern, literal=false, ignore_case=false))]
fn search_reader<'py>(
    py: Python<'py>,
    read: Py<PyAny>,
    sink: Py<PyAny>,
    pattern: String,
    literal: bool,
    ignore_case: bool,
) -> PyResult<Bound<'py, PyAny>> {
    let locals = pyo3_async_runtimes::tokio::get_current_locals(py)?;
    let matcher = grep_regex::RegexMatcherBuilder::new()
        .line_terminator(Some(b'\n'))
        .fixed_strings(literal)
        .case_insensitive(ignore_case)
        .build(&pattern)
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
    pyo3_async_runtimes::tokio::future_into_py(py, async move {
        let result = pyo3_async_runtimes::tokio::get_runtime()
            .spawn_blocking(move || {
                let mut callbacks = SearchCallbacks {
                    read,
                    sink,
                    locals,
                    offset: 0,
                };
                // Sink 和 Reader 共享不可变回调；Reader 偏移单独拥有。
                let mut matches = 0;
                let mut batches = 0;
                let sink_callbacks = SearchCallbacks {
                    read: Python::attach(|py| callbacks.read.clone_ref(py)),
                    sink: Python::attach(|py| callbacks.sink.clone_ref(py)),
                    locals: callbacks.locals.clone(),
                    offset: 0,
                };
                let mut searcher = grep_searcher::SearcherBuilder::new()
                    .bom_sniffing(false)
                    .multi_line(false)
                    .line_number(true)
                    .build();
                searcher
                    .search_reader(
                        &matcher,
                        &mut callbacks,
                        SearchSink { callbacks: &sink_callbacks, matches: &mut matches, batches: &mut batches,
                            pending: Vec::new(), pending_bytes: 0 },
                    )
                    .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;
                Ok::<_, PyErr>((matches, callbacks.offset, batches))
            })
            .await
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))??;
        Python::attach(|py| {
            let value = PyDict::new(py);
            value.set_item("matches", result.0)?;
            value.set_item("bytes_read", result.1)?;
            value.set_item("sink_batches", result.2)?;
            Ok(value.unbind())
        })
    })
}
