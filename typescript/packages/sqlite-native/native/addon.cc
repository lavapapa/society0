#define SQLITE_ENABLE_SESSION 1
#include <sqlite3.h>
#include <node_api.h>
#include <dlfcn.h>
#include <unistd.h>
#include <cerrno>
#include <chrono>
#include <cstdint>
#include <cstring>
#include <cctype>
#include <limits>
#include <filesystem>
#include <mutex>
#include <stdexcept>
#include <string>
#include <vector>
#include <set>
#include <algorithm>

// 所有句柄与分配器来自同一SQLite实例，避免Node内置引擎的符号抢占。
#define APIS(X) \
X(sqlite3_open_v2) X(sqlite3_close) X(sqlite3_close_v2) X(sqlite3_exec) X(sqlite3_errstr) X(sqlite3_errmsg) X(sqlite3_errcode) X(sqlite3_extended_errcode) X(sqlite3_extended_result_codes) \
X(sqlite3_prepare_v2) X(sqlite3_step) X(sqlite3_finalize) X(sqlite3_reset) X(sqlite3_clear_bindings) X(sqlite3_stmt_readonly) \
X(sqlite3_bind_parameter_count) X(sqlite3_bind_null) X(sqlite3_bind_double) X(sqlite3_bind_int64) X(sqlite3_bind_text) X(sqlite3_bind_blob) X(sqlite3_bind_zeroblob) \
X(sqlite3_column_count) X(sqlite3_column_name) X(sqlite3_column_type) X(sqlite3_column_bytes) X(sqlite3_column_blob) X(sqlite3_column_text) X(sqlite3_column_int64) X(sqlite3_column_double) \
X(sqlite3_changes64) X(sqlite3_last_insert_rowid) X(sqlite3_get_autocommit) X(sqlite3_limit) X(sqlite3_set_authorizer) X(sqlite3_progress_handler) X(sqlite3_db_config) \
X(sqlite3session_create) X(sqlite3session_attach) X(sqlite3session_delete) X(sqlite3session_memory_used) X(sqlite3session_changeset_strm) X(sqlite3changeset_apply_v2_strm) \
X(sqlite3_blob_open) X(sqlite3_blob_bytes) X(sqlite3_blob_read) X(sqlite3_blob_close) \
X(sqlite3_backup_init) X(sqlite3_backup_step) X(sqlite3_backup_finish) X(sqlite3_backup_pagecount) \
X(sqlite3_memory_used) X(sqlite3_memory_highwater) X(sqlite3_hard_heap_limit64) X(sqlite3_libversion) X(sqlite3_sourceid)
#define DECL(n) static decltype(&n) p_##n;
APIS(DECL)
using Clock=std::chrono::steady_clock;
struct Failure:std::exception {std::string code,message;int sqliteCode;Failure(std::string c,std::string m,int s=0):code(c),message(m),sqliteCode(s){};};
static void need(bool yes,const char* msg){if(!yes)throw Failure("SQLITE_ARGUMENT",msg);}
static void js(napi_status rc){if(rc!=napi_ok)throw Failure("SQLITE_NAPI","N-API conversion failed");}
static napi_value string(napi_env e,const std::string&s){napi_value v;js(napi_create_string_utf8(e,s.data(),s.size(),&v));return v;}
static napi_value number(napi_env e,int64_t x){napi_value v;js(napi_create_int64(e,x,&v));return v;}
static napi_value bigint(napi_env e,int64_t x){napi_value v;js(napi_create_bigint_int64(e,x,&v));return v;}
static napi_value boolean(napi_env e,bool x){napi_value v;js(napi_get_boolean(e,x,&v));return v;}
static napi_value object(napi_env e){napi_value v;js(napi_create_object(e,&v));return v;}
static void put(napi_env e,napi_value o,const char*k,napi_value v){js(napi_set_named_property(e,o,k,v));}
static napi_value property(napi_env e,napi_value o,const char*k){napi_value v;js(napi_get_named_property(e,o,k,&v));return v;}
static std::string text(napi_env e,napi_value v){size_t len;js(napi_get_value_string_utf8(e,v,nullptr,0,&len));std::vector<char>b(len+1);js(napi_get_value_string_utf8(e,v,b.data(),b.size(),&len));return {b.data(),len};}
static int64_t integer(napi_env e,napi_value v){double n;js(napi_get_value_double(e,v,&n));need(n>=0&&n<=9007199254740991.0&&n==(int64_t)n,"Expected nonnegative safe integer");return (int64_t)n;}
static uint32_t length(napi_env e,napi_value a){uint32_t n;js(napi_get_array_length(e,a,&n));return n;}
static napi_value element(napi_env e,napi_value a,uint32_t i){napi_value v;js(napi_get_element(e,a,i,&v));return v;}
static std::string errorCode(int rc) {
 struct ErrorName { int code; const char* name; };
 static const ErrorName names[]={
  {SQLITE_NOMEM,"SQLITE_NOMEM"},{SQLITE_INTERRUPT,"SQLITE_INTERRUPT"},
  {SQLITE_CONSTRAINT,"SQLITE_CONSTRAINT"},{SQLITE_BUSY,"SQLITE_BUSY"},
  {SQLITE_LOCKED,"SQLITE_LOCKED"},{SQLITE_TOOBIG,"SQLITE_TOOBIG"},
  {SQLITE_AUTH,"SQLITE_AUTH"},{SQLITE_READONLY,"SQLITE_READONLY"},
  {SQLITE_ABORT,"SQLITE_ABORT"},{SQLITE_IOERR,"SQLITE_IOERR"}
 };
 for(const auto& entry:names)if(entry.code==(rc&255))return entry.name;
 return "SQLITE_ERROR";
}
struct DB {sqlite3*db=nullptr;sqlite3_session*session=nullptr;napi_ref controlRef=nullptr;int32_t*control=nullptr;Clock::time_point deadline;int64_t captureLimit=0;int64_t capturePeak=0;std::string stopReason;std::string denied;bool stepping=false;int64_t tableValidationCount=0,schemaValidationSqlCount=0,sessionAttachCount=0,sessionMetadataPrepareCount=0;};
static void check(DB*d,int rc){if(rc!=SQLITE_OK&&rc!=SQLITE_DONE){std::string message=d&&d->db?p_sqlite3_errmsg(d->db):p_sqlite3_errstr(rc);if(message=="not an error")message=p_sqlite3_errstr(rc);throw Failure(errorCode(rc),message,rc);}}
static bool stopped(DB*d){if(d->control&&__atomic_load_n(d->control,__ATOMIC_SEQ_CST)){d->stopReason="cancelled";return true;}if(Clock::now()>=d->deadline){d->stopReason="deadline";return true;}if(d->session){d->capturePeak=std::max(d->capturePeak,(int64_t)p_sqlite3session_memory_used(d->session));if(d->captureLimit&&d->capturePeak>d->captureLimit){d->stopReason="capture-budget";return true;}}return false;}
static int progress(void*p){return stopped((DB*)p)?1:0;}
static void retire(DB*d){if(d->session){p_sqlite3session_delete(d->session);d->session=nullptr;}}
static void finalize(napi_env e,void*p,void*){auto d=(DB*)p;retire(d);if(d->db)p_sqlite3_close_v2(d->db);if(d->controlRef)napi_delete_reference(e,d->controlRef);delete d;}
static DB* handle(napi_env e,napi_value v){void*p;js(napi_get_value_external(e,v,&p));auto d=(DB*)p;if(!d->db)throw Failure("SQLITE_CLOSED","Connection is closed");return d;}
static void controlSql(DB*d,const char*sql){check(d,p_sqlite3_exec(d->db,sql,nullptr,nullptr,nullptr));}
struct Stmt {sqlite3_stmt*p=nullptr;~Stmt(){if(p)p_sqlite3_finalize(p);}};
static void prepare(DB*d,const std::string&sql,Stmt&s){const char*tail=nullptr;need(sql.find('\0')==std::string::npos,"SQL contains NUL");check(d,p_sqlite3_prepare_v2(d->db,sql.c_str(),sql.size(),&s.p,&tail));need(s.p,"SQL statement is empty");if(tail&&*tail){Stmt extra;check(d,p_sqlite3_prepare_v2(d->db,tail,-1,&extra.p,nullptr));need(!extra.p,"Exactly one SQL statement is required");}}
// Session在preupdate hook内读取表结构；领域SQL的prepare阶段仍拒绝PRAGMA。
static bool sessionMetadata(DB*d,int action,const char*name) {
 return action==SQLITE_PRAGMA&&d->stepping&&name&&
  (std::string(name)=="table_info"||std::string(name)=="table_xinfo");
}
static bool domainAction(int action) {
 switch(action) {
  case SQLITE_SELECT:case SQLITE_READ:case SQLITE_INSERT:case SQLITE_UPDATE:
  case SQLITE_DELETE:case SQLITE_FUNCTION:case SQLITE_RECURSIVE:return true;
  default:return false;
 }
}
static int authorize(void*context,int action,const char*name,const char*argument,const char*,const char*) {
 auto d=(DB*)context;
 if(sessionMetadata(d,action,name)){d->sessionMetadataPrepareCount++;return SQLITE_OK;}
 if(domainAction(action))return SQLITE_OK;
 d->denied=std::to_string(action)+":"+(name?name:"")+":"+(argument?argument:"");
 return SQLITE_DENY;
}
struct Authorizer {DB*d;Authorizer(DB*x):d(x){check(d,p_sqlite3_set_authorizer(d->db,authorize,d));}~Authorizer(){p_sqlite3_set_authorizer(d->db,nullptr,nullptr);}};
static void bind(napi_env e,DB*d,sqlite3_stmt*s,napi_value values){uint32_t count=length(e,values);need(count==(uint32_t)p_sqlite3_bind_parameter_count(s),"Parameter count does not match statement");for(uint32_t i=0;i<count;i++){auto v=element(e,values,i);napi_valuetype type;js(napi_typeof(e,v,&type));int rc;
 if(type==napi_null)rc=p_sqlite3_bind_null(s,i+1);
 else if(type==napi_bigint){int64_t x;bool exact;js(napi_get_value_bigint_int64(e,v,&x,&exact));need(exact,"BigInt exceeds SQLite signed 64-bit range");rc=p_sqlite3_bind_int64(s,i+1,x);}
 else if(type==napi_number){double x;js(napi_get_value_double(e,v,&x));rc=p_sqlite3_bind_double(s,i+1,x);}
 else if(type==napi_string){auto x=text(e,v);need(x.size()<=INT32_MAX,"TEXT too large");rc=p_sqlite3_bind_text(s,i+1,x.data(),x.size(),SQLITE_TRANSIENT);}
 else {bool typed=false;js(napi_is_typedarray(e,v,&typed));need(typed,"Expected SQLite value");napi_typedarray_type t;size_t len,offset;void*bytes;napi_value buffer;js(napi_get_typedarray_info(e,v,&t,&len,&bytes,&buffer,&offset));need(t==napi_uint8_array&&len<=INT32_MAX,"BLOB must be Uint8Array");rc=len?p_sqlite3_bind_blob(s,i+1,bytes,len,SQLITE_TRANSIENT):p_sqlite3_bind_zeroblob(s,i+1,0);}
 check(d,rc);
}}
static int step(DB*d,sqlite3_stmt*s){d->stepping=true;int rc=p_sqlite3_step(s);d->stepping=false;return rc;}
struct Columns {std::vector<std::string> names;int64_t keyBytes=0;};
static Columns describeColumns(sqlite3_stmt*s) {
 Columns columns;std::set<std::string>seen;
 for(int c=0;c<p_sqlite3_column_count(s);c++) {
  const char*n=p_sqlite3_column_name(s,c);
  if(!n)throw Failure("SQLITE_NOMEM","Column name allocation failed",SQLITE_NOMEM);
  columns.names.push_back(n);
  need(seen.insert(n).second,"Duplicate column names require SQL aliases");
  columns.keyBytes+=columns.names.back().size();
 }
 return columns;
}
static int64_t rowBytes(DB*d,sqlite3_stmt*s,const Columns&columns) {
 int64_t bytes=columns.keyBytes;
 for(size_t c=0;c<columns.names.size();c++) {
  int type=p_sqlite3_column_type(s,c);
  bytes+=(type==SQLITE_BLOB||type==SQLITE_TEXT)?p_sqlite3_column_bytes(s,c):8;
  if(p_sqlite3_errcode(d->db)==SQLITE_NOMEM)check(d,SQLITE_NOMEM);
 }
 return bytes;
}
static napi_value columnValue(napi_env e,DB*d,sqlite3_stmt*s,int c) {
 napi_value v;int type=p_sqlite3_column_type(s,c);
 if(type==SQLITE_INTEGER)return bigint(e,p_sqlite3_column_int64(s,c));
 if(type==SQLITE_FLOAT){js(napi_create_double(e,p_sqlite3_column_double(s,c),&v));return v;}
 if(type==SQLITE_NULL){js(napi_get_null(e,&v));return v;}
 int size=p_sqlite3_column_bytes(s,c);
 const void*data=type==SQLITE_BLOB?p_sqlite3_column_blob(s,c):p_sqlite3_column_text(s,c);
 if(p_sqlite3_errcode(d->db)==SQLITE_NOMEM)check(d,SQLITE_NOMEM);
 if(type==SQLITE_BLOB)js(napi_create_buffer_copy(e,size,data,nullptr,&v));
 else js(napi_create_string_utf8(e,(const char*)data,size,&v));
 return v;
}
static napi_value rowObject(napi_env e,DB*d,sqlite3_stmt*s,const Columns&columns) {
 auto row=object(e);
 for(size_t c=0;c<columns.names.size();c++) {
  auto v=columnValue(e,d,s,c);
  napi_property_descriptor column={columns.names[c].c_str(),nullptr,nullptr,nullptr,nullptr,v,
   (napi_property_attributes)(napi_writable|napi_enumerable|napi_configurable),nullptr};
  js(napi_define_properties(e,row,1,&column));
 }
 return row;
}
static napi_value rows(napi_env e,DB*d,sqlite3_stmt*s,int64_t maxRows,int64_t maxBytes) {
 napi_value out;js(napi_create_array(e,&out));auto columns=describeColumns(s);
 int64_t count=0,total=0;int rc;
 while((rc=step(d,s))==SQLITE_ROW) {
  if(count>=maxRows)throw Failure("SQLITE_RESULT_LIMIT","Query exceeds maxRows");
  int64_t bytes=rowBytes(d,s,columns);
  if(bytes>maxBytes-total)throw Failure("SQLITE_RESULT_LIMIT","Query exceeds maxBytes");
  total+=bytes;
  js(napi_set_element(e,out,count++,rowObject(e,d,s,columns)));
 }
 check(d,rc);return out;
}
// 空表仍须由SQLite编译器解析父表和父键；只prepare，不执行INSERT。
static void schemaForeignKeys(DB*d){Stmt tables;prepare(d,"SELECT s.name FROM sqlite_schema AS s WHERE s.type='table' AND EXISTS(SELECT 1 FROM pragma_foreign_key_list(s.name))",tables);int rc;while((rc=p_sqlite3_step(tables.p))==SQLITE_ROW){std::string name=(const char*)p_sqlite3_column_text(tables.p,0),quoted="\"";for(char c:name){quoted+=c;if(c=='\"')quoted+=c;}quoted+="\"";Stmt insertion;prepare(d,"INSERT INTO "+quoted+" DEFAULT VALUES",insertion);}check(d,rc);}
static void foreignKeys(DB*d){Stmt f;prepare(d,"PRAGMA foreign_key_check",f);int rc=p_sqlite3_step(f.p);if(rc==SQLITE_ROW){std::string table=(const char*)p_sqlite3_column_text(f.p,0),parent=(const char*)p_sqlite3_column_text(f.p,2);std::string row=p_sqlite3_column_type(f.p,1)==SQLITE_NULL?"WITHOUT ROWID":std::to_string(p_sqlite3_column_int64(f.p,1));throw Failure("SQLITE_CONSTRAINT","Foreign key violation: "+table+" -> "+parent+", rowid="+row,SQLITE_CONSTRAINT_FOREIGNKEY);}check(d,rc);}
struct Stream {DB*d;int fd;int chunk;int64_t maxBytes,bytes=0,calls=0,maxNativeChunk=0,maxIoChunk=0;};
static int output(void*p,const void*data,int n){auto s=(Stream*)p;s->calls++;s->maxNativeChunk=std::max(s->maxNativeChunk,(int64_t)n);if(n>s->maxBytes-s->bytes)return SQLITE_TOOBIG;for(int offset=0;offset<n;){if(stopped(s->d))return SQLITE_INTERRUPT;int size=std::min(s->chunk,n-offset);ssize_t done=write(s->fd,(const char*)data+offset,size);if(done<0&&errno==EINTR)continue;if(done<=0)return SQLITE_IOERR_WRITE;offset+=done;s->bytes+=done;s->maxIoChunk=std::max(s->maxIoChunk,(int64_t)done);}return SQLITE_OK;}
static int input(void*p,void*data,int*n){auto s=(Stream*)p;if(stopped(s->d))return SQLITE_INTERRUPT;int size=std::min(*n,s->chunk);ssize_t got;do{got=read(s->fd,data,size);}while(got<0&&errno==EINTR&&!stopped(s->d));if(got<0)return SQLITE_IOERR_READ;if(got>s->maxBytes-s->bytes)return SQLITE_TOOBIG;*n=got;s->bytes+=got;s->calls++;s->maxIoChunk=std::max(s->maxIoChunk,(int64_t)got);return SQLITE_OK;}
static int conflict(void*,int,sqlite3_changeset_iter*){return SQLITE_CHANGESET_ABORT;}
static napi_value streamResult(napi_env e,const Stream&s){auto r=object(e);put(e,r,"bytes",number(e,s.bytes));put(e,r,"calls",number(e,s.calls));put(e,r,"maxNativeChunk",number(e,s.maxNativeChunk));put(e,r,"maxIoChunk",number(e,s.maxIoChunk));return r;}
static napi_value state(napi_env e,DB*d){auto r=object(e);put(e,r,"autocommit",boolean(e,p_sqlite3_get_autocommit(d->db)));put(e,r,"sessionBytes",number(e,d->session?p_sqlite3session_memory_used(d->session):0));put(e,r,"capturePeak",number(e,d->capturePeak));put(e,r,"stopReason",string(e,d->stopReason));put(e,r,"authorizationDenied",string(e,d->denied));put(e,r,"tableValidationCount",number(e,d->tableValidationCount));put(e,r,"schemaValidationSqlCount",number(e,d->schemaValidationSqlCount));put(e,r,"sessionAttachCount",number(e,d->sessionAttachCount));put(e,r,"sessionMetadataPrepareCount",number(e,d->sessionMetadataPrepareCount));return r;}
// 生命周期操作保持原先顺序：close允许重复，回滚不受预算/取消阻挡。
static napi_value closeOperation(napi_env e,napi_value external) {
 void*p;js(napi_get_value_external(e,external,&p));auto d=(DB*)p;
 if(d->db) {
  retire(d);p_sqlite3_progress_handler(d->db,0,nullptr,nullptr);
  check(d,p_sqlite3_close(d->db));d->db=nullptr;
 }
 return boolean(e,true);
}
static napi_value rollbackOperation(napi_env e,DB*d) {
 p_sqlite3_progress_handler(d->db,0,nullptr,nullptr);
 if(!p_sqlite3_get_autocommit(d->db))controlSql(d,"ROLLBACK");
 return boolean(e,true);
}
static void operationBudget(napi_env e,DB*d,napi_value options) {
 d->deadline=Clock::now()+std::chrono::milliseconds(integer(e,property(e,options,"timeoutMs")));
 d->captureLimit=integer(e,property(e,options,"maxCapturedBytes"));
 d->capturePeak=0;d->stopReason.clear();
 p_sqlite3_progress_handler(d->db,1000,progress,d);
 if(stopped(d))check(d,SQLITE_INTERRUPT);
}
static napi_value setControlOperation(napi_env e,DB*d,napi_value*a) {
 napi_typedarray_type type;size_t count,offset;void*data;napi_value buffer;
 js(napi_get_typedarray_info(e,a[3],&type,&count,&data,&buffer,&offset));
 need(type==napi_int32_array&&count>=1,"control must be Int32Array");
 if(d->controlRef)napi_delete_reference(e,d->controlRef);
 d->control=(int32_t*)data;js(napi_create_reference(e,a[3],1,&d->controlRef));return boolean(e,true);
}
static napi_value beginOperation(napi_env e,DB*d,napi_value*) {
 controlSql(d,"BEGIN IMMEDIATE");return boolean(e,true);
}
static napi_value commitOperation(napi_env e,DB*d,napi_value*) {
 controlSql(d,"COMMIT");return boolean(e,true);
}
static napi_value snapshotOperation(napi_env e,DB*d,napi_value*) {
 controlSql(d,"BEGIN");Stmt s;prepare(d,"SELECT rootpage FROM sqlite_schema LIMIT 1",s);
 int rc=p_sqlite3_step(s.p);if(rc!=SQLITE_ROW)check(d,rc);
 return boolean(e,true);
}
static napi_value foreignKeysOperation(napi_env e,DB*d,napi_value*) {
 schemaForeignKeys(d);foreignKeys(d);return boolean(e,true);
}
static napi_value schemaOperation(napi_env e,DB*d,napi_value*a) {
 auto sql=text(e,a[3]);need(sql.find('\0')==std::string::npos,"Schema SQL contains NUL");
 controlSql(d,sql.c_str());return boolean(e,true);
}
static napi_value executeStatement(napi_env e,DB*d,sqlite3_stmt*s,napi_value values,bool many) {
 int64_t changes=0;uint32_t count=many?length(e,values):1;
 for(uint32_t i=0;i<count;i++) {
  if(stopped(d))check(d,SQLITE_INTERRUPT);
  bind(e,d,s,many?element(e,values,i):values);
  int rc=step(d,s);
  if(rc==SQLITE_ROW)throw Failure("SQLITE_ARGUMENT","Use execute without RETURNING");
  check(d,rc);changes+=p_sqlite3_changes64(d->db);
  check(d,p_sqlite3_reset(s));check(d,p_sqlite3_clear_bindings(s));
 }
 if(stopped(d))check(d,SQLITE_INTERRUPT);
 auto r=object(e);put(e,r,"changes",bigint(e,changes));
 put(e,r,"lastInsertRowid",bigint(e,p_sqlite3_last_insert_rowid(d->db)));return r;
}
enum class SqlOperation {query,execute,executeMany};
static napi_value sqlOperation(napi_env e,DB*d,napi_value*a,SqlOperation op) {
 Authorizer auth(d);Stmt s;prepare(d,text(e,a[3]),s);
 if(op==SqlOperation::query) {
  need(p_sqlite3_stmt_readonly(s.p),"query requires readonly SQL");bind(e,d,s.p,a[4]);
  return rows(e,d,s.p,integer(e,property(e,a[5],"maxRows")),integer(e,property(e,a[5],"maxBytes")));
 }
 return executeStatement(e,d,s.p,a[4],op==SqlOperation::executeMany);
}
static napi_value queryOperation(napi_env e,DB*d,napi_value*a) {
 return sqlOperation(e,d,a,SqlOperation::query);
}
static napi_value executeOperation(napi_env e,DB*d,napi_value*a) {
 return sqlOperation(e,d,a,SqlOperation::execute);
}
static napi_value executeManyOperation(napi_env e,DB*d,napi_value*a) {
 return sqlOperation(e,d,a,SqlOperation::executeMany);
}
static napi_value validateTableOperation(napi_env e,DB*d,napi_value*a) {
 d->tableValidationCount++;auto table=text(e,a[3]);Stmt kind;d->schemaValidationSqlCount++;
 prepare(d,"SELECT type FROM pragma_table_list WHERE schema='main' AND name=?",kind);
 check(d,p_sqlite3_bind_text(kind.p,1,table.c_str(),table.size(),SQLITE_TRANSIENT));
 need(p_sqlite3_step(kind.p)==SQLITE_ROW,"Session table does not exist");
 need(std::string((const char*)p_sqlite3_column_text(kind.p,0))=="table","Session supports ordinary tables");
 Stmt columns;d->schemaValidationSqlCount++;
 prepare(d,"SELECT pk,\"notnull\",type,hidden FROM pragma_table_xinfo(?)",columns);
 check(d,p_sqlite3_bind_text(columns.p,1,table.c_str(),table.size(),SQLITE_TRANSIENT));
 int rc,pk=0,nullable=0;bool integerPk=false;
 while((rc=p_sqlite3_step(columns.p))==SQLITE_ROW) {
  need(p_sqlite3_column_int64(columns.p,3)==0,"Generated columns require separate restore support");
  if(p_sqlite3_column_int64(columns.p,0)) {
   pk++;nullable+=!p_sqlite3_column_int64(columns.p,1);
   std::string type=(const char*)p_sqlite3_column_text(columns.p,2);
   for(char&c:type)c=std::toupper((unsigned char)c);
   integerPk=type=="INTEGER";
  }
 }
 check(d,rc);need(pk>0,"Session tables require an explicit primary key");
 if(nullable) {
  Stmt indices;d->schemaValidationSqlCount++;
  prepare(d,"SELECT count(*) FROM pragma_index_list(?) WHERE origin='pk'",indices);
  check(d,p_sqlite3_bind_text(indices.p,1,table.c_str(),table.size(),SQLITE_TRANSIENT));
  need(p_sqlite3_step(indices.p)==SQLITE_ROW,"Cannot inspect primary key");
  need(pk==1&&integerPk&&p_sqlite3_column_int64(indices.p,0)==0,"Session primary key must be NOT NULL or a rowid alias");
 }
 return boolean(e,true);
}
static napi_value startSessionOperation(napi_env e,DB*d,napi_value*a) {
 need(!d->session,"Session already active");check(d,p_sqlite3session_create(d->db,"main",&d->session));
 try {
  for(uint32_t i=0;i<length(e,a[3]);i++) {
   d->sessionAttachCount++;
   check(d,p_sqlite3session_attach(d->session,text(e,element(e,a[3],i)).c_str()));
  }
 }catch(...){retire(d);throw;}
 return boolean(e,true);
}
static napi_value exportOperation(napi_env e,DB*d,napi_value*a) {
 need(d->session,"Session is missing");
 Stream s{d,(int)integer(e,a[3]),(int)integer(e,a[4]),integer(e,a[5])};
 check(d,p_sqlite3session_changeset_strm(d->session,output,&s));return streamResult(e,s);
}
static napi_value applyOperation(napi_env e,DB*d,napi_value*a) {
 need(!d->session,"Cannot apply with active capture");
 Stream s{d,(int)integer(e,a[3]),(int)integer(e,a[4]),integer(e,a[5])};int triggers;
 check(d,p_sqlite3_db_config(d->db,SQLITE_DBCONFIG_ENABLE_TRIGGER,-1,&triggers));
 controlSql(d,"SAVEPOINT society0_apply");
 try {
  check(d,p_sqlite3_db_config(d->db,SQLITE_DBCONFIG_ENABLE_TRIGGER,0,nullptr));
  check(d,p_sqlite3changeset_apply_v2_strm(d->db,input,&s,nullptr,conflict,nullptr,nullptr,nullptr,SQLITE_CHANGESETAPPLY_FKNOACTION));
  foreignKeys(d);controlSql(d,"RELEASE society0_apply");
  check(d,p_sqlite3_db_config(d->db,SQLITE_DBCONFIG_ENABLE_TRIGGER,triggers,nullptr));
 }catch(...) {
  p_sqlite3_progress_handler(d->db,0,nullptr,nullptr);
  p_sqlite3_exec(d->db,"ROLLBACK TO society0_apply; RELEASE society0_apply",nullptr,nullptr,nullptr);
  p_sqlite3_db_config(d->db,SQLITE_DBCONFIG_ENABLE_TRIGGER,triggers,nullptr);throw;
 }
 return streamResult(e,s);
}
static napi_value applyManyOperation(napi_env e,DB*d,napi_value*a) {
 need(!d->session,"Cannot apply with active capture");uint32_t count=length(e,a[3]);need(count>0,"applyMany requires at least one changeset");
 int chunk=(int)integer(e,a[4]);int64_t maxBytes=integer(e,a[5]);int triggers;
 check(d,p_sqlite3_db_config(d->db,SQLITE_DBCONFIG_ENABLE_TRIGGER,-1,&triggers));
 controlSql(d,"BEGIN IMMEDIATE");
 try {
  check(d,p_sqlite3_db_config(d->db,SQLITE_DBCONFIG_ENABLE_TRIGGER,0,nullptr));
  controlSql(d,"PRAGMA defer_foreign_keys=ON");
  for(uint32_t i=0;i<count;i++){int fd=(int)integer(e,element(e,a[3],i));Stream s{d,fd,chunk,maxBytes};check(d,p_sqlite3changeset_apply_v2_strm(d->db,input,&s,nullptr,conflict,nullptr,nullptr,nullptr,SQLITE_CHANGESETAPPLY_FKNOACTION));}
  foreignKeys(d);controlSql(d,"COMMIT");
  check(d,p_sqlite3_db_config(d->db,SQLITE_DBCONFIG_ENABLE_TRIGGER,triggers,nullptr));
 }catch(...) {
  p_sqlite3_progress_handler(d->db,0,nullptr,nullptr);
  p_sqlite3_exec(d->db,"ROLLBACK",nullptr,nullptr,nullptr);
  p_sqlite3_db_config(d->db,SQLITE_DBCONFIG_ENABLE_TRIGGER,triggers,nullptr);throw;
 }
 return boolean(e,true);
}
static napi_value blobOperation(napi_env e,DB*d,napi_value*a) {
 auto request=a[3];auto table=text(e,property(e,request,"table")),column=text(e,property(e,request,"column"));
 int64_t rowid;bool exact;js(napi_get_value_bigint_int64(e,property(e,request,"rowid"),&rowid,&exact));
 need(exact,"rowid exceeds int64");
 int64_t offset=integer(e,property(e,request,"offset")),len=integer(e,property(e,request,"length"));
 need(offset<=INT32_MAX&&len<=INT32_MAX,"BLOB range too large");sqlite3_blob*b=nullptr;
 check(d,p_sqlite3_blob_open(d->db,"main",table.c_str(),column.c_str(),rowid,0,&b));
 try {
  need(offset<=p_sqlite3_blob_bytes(b)-len,"BLOB range exceeds value");
  void*bytes;napi_value out;js(napi_create_buffer(e,len,&bytes,&out));
  check(d,p_sqlite3_blob_read(b,bytes,len,offset));
  int end=p_sqlite3_blob_close(b);b=nullptr;check(d,end);return out;
 }catch(...){if(b)p_sqlite3_blob_close(b);throw;}
}
struct BackupResult {int64_t bytes=0,steps=0;};
static BackupResult copyBackupPages(DB*d,DB*target,sqlite3_backup*b,int pages,int64_t pageSize,int64_t maxBytes) {
 BackupResult result;int rc;
 do {
  if(stopped(d))check(d,SQLITE_INTERRUPT);
  rc=p_sqlite3_backup_step(b,pages);result.bytes=(int64_t)p_sqlite3_backup_pagecount(b)*pageSize;
  if(result.bytes>maxBytes)throw Failure("SQLITE_RESULT_LIMIT","Backup exceeds maxBytes");
  result.steps++;if(rc!=SQLITE_OK&&rc!=SQLITE_DONE)check(target,rc);
 }while(rc!=SQLITE_DONE);
 return result;
}
static napi_value backupOperation(napi_env e,DB*d,napi_value*a) {
 auto path=text(e,a[3]);int pages=integer(e,a[4]);int64_t maxBytes=integer(e,a[5]);
 DB target;sqlite3_backup*b=nullptr;
 try {
  check(&target,p_sqlite3_open_v2(path.c_str(),&target.db,SQLITE_OPEN_READWRITE,nullptr));
  b=p_sqlite3_backup_init(target.db,"main",d->db,"main");
  if(!b)check(&target,p_sqlite3_errcode(target.db));
  Stmt size;prepare(d,"PRAGMA page_size",size);
  need(p_sqlite3_step(size.p)==SQLITE_ROW,"Cannot read page size");
  auto result=copyBackupPages(d,&target,b,pages,p_sqlite3_column_int64(size.p,0),maxBytes);
  int finish=p_sqlite3_backup_finish(b);b=nullptr;check(&target,finish);
  check(&target,p_sqlite3_close(target.db));target.db=nullptr;
  auto r=object(e);put(e,r,"bytes",number(e,result.bytes));put(e,r,"steps",number(e,result.steps));return r;
 }catch(...){if(b)p_sqlite3_backup_finish(b);if(target.db)p_sqlite3_close_v2(target.db);throw;}
}
static napi_value runOperation(napi_env e,DB*d,napi_value*a,const std::string&op) {
 struct Operation {const char* name;napi_value (*run)(napi_env,DB*,napi_value*);};
 static const Operation operations[]={
  {"setControl",setControlOperation},{"begin",beginOperation},{"commit",commitOperation},{"beginSnapshot",snapshotOperation},
  {"checkForeignKeys",foreignKeysOperation},{"schema",schemaOperation},
  {"query",queryOperation},{"execute",executeOperation},{"executeMany",executeManyOperation},
  {"validateTable",validateTableOperation},{"startSession",startSessionOperation},
  {"export",exportOperation},{"apply",applyOperation},{"applyMany",applyManyOperation},{"blob",blobOperation},{"backup",backupOperation}
 };
 for(const auto& operation:operations)if(op==operation.name)return operation.run(e,d,a);
 throw Failure("SQLITE_ARGUMENT","Unknown native operation");
}
// 错误上报不能再次抛出 C++ 异常；Worker 终止时 N-API 已禁止构造 JS 值。
static bool errorStatus(napi_env e,napi_status status) noexcept {
 if(status==napi_ok)return true;
 if(status!=napi_pending_exception&&status!=napi_closing&&status!=napi_cannot_run_js)
  napi_throw_error(e,"SQLITE_NAPI","Native error reporting failed");
 return false;
}
static bool errorText(napi_env e,napi_value error,const char*key,const std::string&value) noexcept {
 napi_value text;
 return errorStatus(e,napi_create_string_utf8(e,value.data(),value.size(),&text))&&
  errorStatus(e,napi_set_named_property(e,error,key,text));
}
static bool errorNumber(napi_env e,napi_value error,const char*key,int64_t value) noexcept {
 napi_value number;
 return errorStatus(e,napi_create_int64(e,value,&number))&&
  errorStatus(e,napi_set_named_property(e,error,key,number));
}
static napi_value reportFailure(napi_env e,const Failure&failure) noexcept {
 napi_value message,error;
 if(!errorStatus(e,napi_create_string_utf8(e,failure.message.data(),failure.message.size(),&message)))return nullptr;
 if(!errorStatus(e,napi_create_error(e,nullptr,message,&error)))return nullptr;
 if(!errorText(e,error,"code",failure.code))return nullptr;
 if(!errorNumber(e,error,"sqliteCode",failure.sqliteCode&255))return nullptr;
 if(!errorNumber(e,error,"sqliteExtendedCode",failure.sqliteCode))return nullptr;
 errorStatus(e,napi_throw(e,error));return nullptr;
}
static napi_value dispatch(napi_env e,napi_callback_info info) {
 try {
  size_t argc=8;napi_value a[8];js(napi_get_cb_info(e,info,&argc,a,nullptr,nullptr));
  need(argc>=3,"Missing operation arguments");auto op=text(e,a[1]);
  if(op=="close")return closeOperation(e,a[0]);
  DB*d=handle(e,a[0]);
  if(op=="state")return state(e,d);
  if(op=="retireSession"){retire(d);return boolean(e,true);}
  if(op=="rollback"||op=="endSnapshot")return rollbackOperation(e,d);
  if(op=="setControl")return setControlOperation(e,d,a);
  operationBudget(e,d,a[2]);return runOperation(e,d,a,op);
 }catch(const Failure&x) {return reportFailure(e,x);
 }catch(const std::exception&x){napi_throw_error(e,"SQLITE_NATIVE",x.what());return nullptr;}
}
static napi_value open(napi_env e,napi_callback_info info){DB*d=nullptr;try{size_t argc=8;napi_value a[8];js(napi_get_cb_info(e,info,&argc,a,nullptr,nullptr));need(argc==4||argc==5,"open requires path, readonly, inline limit, control and optional pragmas");d=new DB;auto path=text(e,a[0]);need(path.find('\0')==std::string::npos,"Path contains NUL");bool readonly;js(napi_get_value_bool(e,a[1],&readonly));int flags=readonly?SQLITE_OPEN_READONLY:SQLITE_OPEN_READWRITE|SQLITE_OPEN_CREATE;check(d,p_sqlite3_open_v2(path.c_str(),&d->db,flags|SQLITE_OPEN_NOMUTEX,nullptr));check(d,p_sqlite3_extended_result_codes(d->db,1));p_sqlite3_limit(d->db,SQLITE_LIMIT_LENGTH,integer(e,a[2]));napi_typedarray_type type;size_t count,offset;void*data;napi_value buffer;js(napi_get_typedarray_info(e,a[3],&type,&count,&data,&buffer,&offset));need(type==napi_int32_array&&count>=1,"control must be Int32Array");d->control=(int32_t*)data;js(napi_create_reference(e,a[3],1,&d->controlRef));controlSql(d,"PRAGMA foreign_keys=ON");if(!readonly){bool normal=false;int64_t cacheSize=-65536;if(argc==5){auto options=a[4];auto mode=text(e,property(e,options,"synchronous"));need(mode=="full"||mode=="normal","synchronous must be full or normal");normal=mode=="normal";double cacheNumber;js(napi_get_value_double(e,property(e,options,"cacheSize"),&cacheNumber));need(cacheNumber==(double)(int64_t)cacheNumber&&cacheNumber>=-2147483648.0&&cacheNumber<=2147483647.0&&cacheNumber!=0,"cacheSize must be a non-zero int32");cacheSize=(int64_t)cacheNumber;}controlSql(d,("PRAGMA journal_mode=WAL;PRAGMA synchronous="+std::string(normal?"NORMAL":"FULL")+";PRAGMA cache_size="+std::to_string(cacheSize)+";").c_str());}napi_value out;js(napi_create_external(e,d,finalize,nullptr,&out));return out;
 }catch(const Failure&x){if(d)finalize(e,d,nullptr);napi_throw_error(e,x.code.c_str(),x.message.c_str());return nullptr;}catch(const std::exception&x){if(d)finalize(e,d,nullptr);napi_throw_error(e,"SQLITE_NATIVE",x.what());return nullptr;}}
static napi_value memory(napi_env e,napi_callback_info){try{auto r=object(e);put(e,r,"used",number(e,p_sqlite3_memory_used()));put(e,r,"peak",number(e,p_sqlite3_memory_highwater(0)));put(e,r,"heapLimit",number(e,p_sqlite3_hard_heap_limit64(-1)));put(e,r,"sqliteVersion",string(e,p_sqlite3_libversion()));put(e,r,"sourceIdentity",string(e,p_sqlite3_sourceid()));return r;}catch(...){napi_throw_error(e,"SQLITE_NAPI","Memory diagnostics failed");return nullptr;}}
static napi_value heap(napi_env e,napi_callback_info info){try{size_t argc=1;napi_value a[1];js(napi_get_cb_info(e,info,&argc,a,nullptr,nullptr));return number(e,p_sqlite3_hard_heap_limit64(integer(e,a[0])));}catch(const Failure&x){napi_throw_error(e,x.code.c_str(),x.message.c_str());return nullptr;}}
static napi_value init(napi_env e,napi_value exports){try{static std::once_flag once;std::call_once(once,[]{Dl_info here;need(dladdr((void*)&init,&here)!=0,"Cannot locate native module");auto lib=std::filesystem::path(here.dli_fname).parent_path().parent_path()/"vendor/libsqlite3.so";void*h=dlopen(lib.c_str(),RTLD_NOW|RTLD_LOCAL|RTLD_DEEPBIND);if(!h)throw Failure("SQLITE_LOAD",dlerror());
#define LOAD(n) p_##n=(decltype(p_##n))dlsym(h,#n);need(p_##n,#n " unavailable");
 APIS(LOAD)
 need(std::string(p_sqlite3_libversion())=="3.49.1","Unexpected SQLite version");need(std::string(p_sqlite3_sourceid())=="2025-02-18 13:38:58 873d4e274b4988d260ba8354a9718324a1c26187a4ab4c1cc0227c03d0f10e70","Unexpected SQLite source identity");p_sqlite3_hard_heap_limit64(64LL*1024*1024);});
#define METHOD(n) {#n,nullptr,n,nullptr,nullptr,nullptr,napi_default,nullptr}
 napi_property_descriptor props[]={METHOD(open),METHOD(dispatch),METHOD(memory),METHOD(heap)};js(napi_define_properties(e,exports,4,props));return exports;}catch(const Failure&x){napi_throw_error(e,x.code.c_str(),x.message.c_str());return nullptr;}}
NAPI_MODULE(NODE_GYP_MODULE_NAME,init)
