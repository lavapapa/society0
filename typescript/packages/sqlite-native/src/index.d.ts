export type SqliteValue=null|number|bigint|string|Uint8Array;
export type SqliteRow=Record<string,SqliteValue>;
export interface QueryBounds {maxRows?:number;maxBytes?:number}
export interface StreamBounds {chunkBytes?:number;maxBytes?:number}
export interface StreamResult {bytes:number;calls:number;maxNativeChunk:number;maxIoChunk:number}
export interface ConnectionOptions {path:string;readOnly?:boolean;synchronous?:'full'|'normal';cacheSize?:number;maxInlineBytes?:number;timeoutMs?:number;maxCapturedBytes?:number;maxInputBytes?:number;control?:Int32Array<SharedArrayBuffer>}
export interface ConnectionState {closed:boolean;dirty:boolean;session:'none'|'active'|'retired';autocommit?:boolean;transaction?:boolean;failed?:boolean;pendingSegment?:boolean;sessionBytes?:number;retiredSessionBytes?:number;capturePeak?:number;stopReason?:string;authorizationDenied?:string;tableValidationCount?:number;schemaValidationSqlCount?:number;sessionAttachCount?:number;sessionMetadataPrepareCount?:number;sqlRowsRead?:number;rowsRead?:number;lastError?:{code:string;sqliteCode?:number;message:string;operation:string}}
export declare function createInterruptControl():Int32Array<SharedArrayBuffer>;
/** 全进程同SQLite库实例共享；返回原限额，0表示解除。 */
export declare function configureHeapLimit(bytes:number):number;
export declare function sqliteMemory():{used:number;peak:number;heapLimit:number;sqliteVersion:string;sourceIdentity:string};
export declare function openConnection(options:ConnectionOptions):SqliteConnection;
/** 每个连接由一个worker拥有，所有方法同步执行。 */
export declare class SqliteConnection {
 constructor(options:ConnectionOptions);
 readonly control:Int32Array<SharedArrayBuffer>;
 setControl(control:Int32Array<SharedArrayBuffer>):void;
 state():ConnectionState;
 execSchema(sql:string):void;
 begin(bounds?:{maxDurationMs?:number;maxCapturedBytes?:number}):void;
 commit():void;
 rollback():void;
 /** SQL/TEXT孤立代理抛SQLITE_TEXT_NOT_WELL_FORMED；TEXT消息包含参数下标，绑定前拒绝。 */
 execute(sql:string,parameters?:readonly SqliteValue[]):{changes:bigint;lastInsertRowid:bigint};
 /** 整批SQL/TEXT预检先于执行；SQLITE_TEXT_NOT_WELL_FORMED包含行号和参数下标。 */
 executeMany(sql:string,rows:readonly (readonly SqliteValue[])[],bounds?:{maxRows?:number;maxInputBytes?:number}):{changes:bigint;lastInsertRowid:bigint};
 /** SQL/TEXT拒绝孤立代理；标识、路径仍按SQLITE_ARGUMENT处理。 */
 query(sql:string,parameters?:readonly SqliteValue[],bounds?:QueryBounds):SqliteRow[];
 /** 无违例返回；首个违例抛SQLITE_CONSTRAINT并定位表、父表、rowid。 */
 checkForeignKeys():void;
 beginSnapshot(options?:{maxAgeMs?:number}):void;
 endSnapshot():void;
 readBlobRange(request:{table:string;column:string;rowid:bigint;offset:number;length:number;maxBytes?:number}):Uint8Array;
 startSession(tables:readonly string[]):void;
 retireSession():void;
 exportSegment(path:string,bounds?:StreamBounds):StreamResult;
 applySegment(path:string,bounds?:StreamBounds):StreamResult;
 applyMany(paths:readonly string[],bounds?:StreamBounds):void;
 backupTo(path:string,bounds?:{pagesPerStep?:number;maxBytes?:number}):{bytes:number;steps:number};
 close():void;
}
