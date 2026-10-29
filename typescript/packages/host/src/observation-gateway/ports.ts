/** Host wire contracts; implementation belongs to the borrowed run scope. */
export interface ObservationStatus {
 readonly runId:string;
 readonly state:'clean'|'dirty'|'unknown';
 readonly liveRevision?:string;
 readonly completePoint?:unknown;
 readonly reason?:string;
 readonly database:unknown;
 readonly execution:unknown;
 readonly controller:unknown;
 readonly hostResources:unknown;
 readonly hostDiagnostics:unknown;
 readonly hostPlugins:{readonly total:number;readonly entries:readonly unknown[]};
}
export interface ObservationQuery {
 readonly sql?:string;
 readonly parameters?:readonly (null|string|number)[];
 readonly cursor?:string;
 readonly pageSize?:number;
}
export interface ObservationPage {
 readonly snapshot:string;readonly revision:string;
 /** SQLite integers and blobs are encoded as {integer:string}/{base64:string}. */
 readonly rows:readonly Readonly<Record<string,unknown>>[];
 readonly cursor?:string;
}
export interface ObservationEvent {readonly sequence:string;readonly dropped:number;readonly status:ObservationStatus}
export interface ObservationStream extends AsyncIterableIterator<ObservationEvent> {close():Promise<void>}
export interface ObservationPort {
 status():Promise<ObservationStatus>;
 query(request:ObservationQuery,signal?:AbortSignal):Promise<ObservationPage>;
 subscribe(options?:{signal?:AbortSignal}):ObservationStream;
 closeCursor(cursor:string):Promise<void>;
}
