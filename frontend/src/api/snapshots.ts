import {request,post} from './client';import type {Snapshot,Utterance} from '../types/domain';
export interface UploadSession{id:number;snapshot_id:number;name:string;kind:string;filename:string;status:string;total_batches:number;received_batches:number;row_count:number;error:string|null;created_at:string;updated_at:string}
export const snapshotApi={
 all:()=>request<Snapshot[]>('/snapshots'),
 features:()=>request<{numeric:Record<string,string>;categorical:Record<string,string>}>('/features'),
 utterances:(id:number)=>request<Utterance[]>('/snapshots/'+id+'/utterances'),
 create:(data:any)=>post('/snapshots',data),
 uploads:()=>request<UploadSession[]>('/uploads'),
 initUpload:(data:any)=>post('/uploads',data),
 uploadBatch:(id:number,data:any)=>post('/uploads/'+id+'/batches',data),
 publishUpload:(id:number,total_batches:number)=>request('/uploads/'+id+'/publish',{method:'POST',body:JSON.stringify({total_batches})}),
 upload:(id:number)=>request<UploadSession>('/uploads/'+id)
};
