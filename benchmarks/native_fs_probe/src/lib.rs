//! 试验桥：原生 OverlayFs 负责文件语义，适配器导出变更与删除路径。
use std::{path::{Path,PathBuf},sync::{Arc,Mutex},time::SystemTime};
use bashkit::{FileSystem,FileSystemExt,OverlayFs,Metadata,DirEntry,Result,VfsEntryKind};
use bashkit::interop::fs::{BashkitFsAbiOwnedHandleV1,export_filesystem,import_owned_filesystem};
use pyo3::{prelude::*,types::{PyCapsule,PyBytes,PyDict,PyList}};
use async_trait::async_trait;

struct DeltaFs { inner:OverlayFs, removed:Mutex<Vec<PathBuf>> }
#[async_trait]
impl FileSystemExt for DeltaFs {}
#[async_trait]
impl FileSystem for DeltaFs {
 async fn read_file(&self,p:&Path)->Result<Vec<u8>>{self.inner.read_file(p).await}
 async fn write_file(&self,p:&Path,c:&[u8])->Result<()>{self.inner.write_file(p,c).await}
 async fn append_file(&self,p:&Path,c:&[u8])->Result<()>{self.inner.append_file(p,c).await}
 async fn mkdir(&self,p:&Path,r:bool)->Result<()>{self.inner.mkdir(p,r).await}
 async fn remove(&self,p:&Path,r:bool)->Result<()>{self.inner.remove(p,r).await?;self.removed.lock().unwrap().push(p.into());Ok(())}
 async fn stat(&self,p:&Path)->Result<Metadata>{self.inner.stat(p).await}
 async fn read_dir(&self,p:&Path)->Result<Vec<DirEntry>>{self.inner.read_dir(p).await}
 async fn exists(&self,p:&Path)->Result<bool>{self.inner.exists(p).await}
 async fn rename(&self,a:&Path,b:&Path)->Result<()>{self.inner.rename(a,b).await?;self.removed.lock().unwrap().push(a.into());Ok(())}
 async fn copy(&self,a:&Path,b:&Path)->Result<()>{self.inner.copy(a,b).await}
 async fn symlink(&self,a:&Path,b:&Path)->Result<()>{self.inner.symlink(a,b).await}
 async fn read_link(&self,p:&Path)->Result<PathBuf>{self.inner.read_link(p).await}
 async fn chmod(&self,p:&Path,m:u32)->Result<()>{self.inner.chmod(p,m).await}
 async fn set_modified_time(&self,p:&Path,t:SystemTime)->Result<()>{self.inner.set_modified_time(p,t).await}
}
#[pyclass]
struct Overlay { fs:Arc<DeltaFs> }
#[pymethods]
impl Overlay {
 #[new]
 fn new(lower:Bound<'_,PyCapsule>)->PyResult<Self>{
  let pointer=lower.pointer_checked(Some(c"bashkit.FileSystem.v1"))?;
  let handle=unsafe{&*pointer.as_ptr().cast::<BashkitFsAbiOwnedHandleV1>()};
  let lower=unsafe{import_owned_filesystem(handle)}.map_err(|e|pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;
  Ok(Self{fs:Arc::new(DeltaFs{inner:OverlayFs::new(lower),removed:Mutex::new(Vec::new())})})
 }
 fn capsule<'py>(&self,py:Python<'py>)->PyResult<Bound<'py,PyCapsule>>{
  let handle=export_filesystem(self.fs.clone()).map_err(|e|pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;
  PyCapsule::new_with_value(py,handle,c"bashkit.FileSystem.v1")
 }
 fn changes<'py>(&self,py:Python<'py>)->PyResult<Bound<'py,PyDict>>{
  let result=PyDict::new(py);let entries=PyList::empty(py);
  for entry in self.fs.inner.upper().snapshot().entries(){
   let item=PyDict::new(py);item.set_item("path",entry.path.to_string_lossy().as_ref())?;item.set_item("mode",entry.mode)?;
   match &entry.kind {
    VfsEntryKind::File{content}=>{item.set_item("kind","file")?;item.set_item("content",PyBytes::new(py,content))?;},
    VfsEntryKind::Directory=>{item.set_item("kind","directory")?;},
    VfsEntryKind::Symlink{target}=>{item.set_item("kind","symlink")?;item.set_item("target",target.to_string_lossy().as_ref())?;},
    VfsEntryKind::Fifo=>{item.set_item("kind","fifo")?;},
   }
   entries.append(item)?;
  }
  result.set_item("entries",entries)?;
  result.set_item("removed",self.fs.removed.lock().unwrap().iter().map(|p|p.to_string_lossy().into_owned()).collect::<Vec<_>>())?;
  Ok(result)
 }
}
#[pymodule]
fn society0_fs_probe(m:&Bound<'_,PyModule>)->PyResult<()>{m.add_class::<Overlay>()?;m.add_function(wrap_pyfunction!(callback_filesystem,m)?)?;Ok(())}

// 只读下层仅桥接到调用者事件循环，目录和正文由成熟数据源提供。
struct CallbackFs { callback:Py<PyAny>, locals:pyo3_async_runtimes::TaskLocals }
fn io_error(e:PyErr)->bashkit::Error { std::io::Error::other(e.to_string()).into() }
fn readonly()->bashkit::Error { std::io::Error::new(std::io::ErrorKind::PermissionDenied,"read-only filesystem").into() }
fn metadata(value:(bool,u64,u32))->Metadata {
 Metadata{file_type:if value.0 {bashkit::FileType::Directory}else{bashkit::FileType::File},size:value.1,mode:value.2,..Metadata::default()}
}
impl CallbackFs {
 async fn call(&self,op:&str,path:&Path)->Result<Py<PyAny>> {
  let future=Python::attach(|py| {
   let awaitable=self.callback.bind(py).call1((op,path.to_string_lossy().as_ref()))?;
   pyo3_async_runtimes::into_future_with_locals(&self.locals,awaitable)
  }).map_err(io_error)?;
  future.await.map_err(io_error)
 }
}
#[async_trait]
impl FileSystemExt for CallbackFs {}
#[async_trait]
impl FileSystem for CallbackFs {
 async fn read_file(&self,p:&Path)->Result<Vec<u8>> {let value=self.call("read",p).await?;Python::attach(|py|value.extract(py)).map_err(io_error)}
 async fn stat(&self,p:&Path)->Result<Metadata> {let value=self.call("stat",p).await?;Python::attach(|py|value.extract(py)).map(metadata).map_err(io_error)}
 async fn read_dir(&self,p:&Path)->Result<Vec<DirEntry>> {let value=self.call("list",p).await?;let rows:Vec<(String,bool,u64,u32)>=Python::attach(|py|value.extract(py)).map_err(io_error)?;Ok(rows.into_iter().map(|(name,d,s,m)|DirEntry{name,metadata:metadata((d,s,m))}).collect())}
 async fn exists(&self,p:&Path)->Result<bool> {let value=self.call("exists",p).await?;Python::attach(|py|value.extract(py)).map_err(io_error)}
 async fn read_link(&self,p:&Path)->Result<PathBuf>{let value=self.call("read_link",p).await?;let path:String=Python::attach(|py|value.extract(py)).map_err(io_error)?;Ok(path.into())}
 async fn write_file(&self,_:&Path,_:&[u8])->Result<()>{Err(readonly())}
 async fn append_file(&self,_:&Path,_:&[u8])->Result<()>{Err(readonly())}
 async fn mkdir(&self,_:&Path,_:bool)->Result<()>{Err(readonly())}
 async fn remove(&self,_:&Path,_:bool)->Result<()>{Err(readonly())}
 async fn rename(&self,_:&Path,_:&Path)->Result<()>{Err(readonly())}
 async fn copy(&self,_:&Path,_:&Path)->Result<()>{Err(readonly())}
 async fn symlink(&self,_:&Path,_:&Path)->Result<()>{Err(readonly())}
 async fn chmod(&self,_:&Path,_:u32)->Result<()>{Err(readonly())}
 async fn set_modified_time(&self,_:&Path,_:SystemTime)->Result<()>{Err(readonly())}
}
#[pyfunction]
fn callback_filesystem<'py>(py:Python<'py>,callback:Py<PyAny>)->PyResult<Bound<'py,PyCapsule>> {
 let locals=pyo3_async_runtimes::tokio::get_current_locals(py)?;
 let handle=export_filesystem(Arc::new(CallbackFs{callback,locals})).map_err(|e|pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;
 PyCapsule::new_with_value(py,handle,c"bashkit.FileSystem.v1")
}
