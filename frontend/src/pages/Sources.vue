<script setup lang="ts">
import {ref,computed,onMounted,onUnmounted} from 'vue';
import type {Snapshot,Utterance,User,UploadSession} from '../types/domain';
import {snapshotApi} from '../api/snapshots';
import {useAsync} from '../hooks/useAsync';
import PageTitle from '../components/common/PageTitle.vue';
import Table from '../components/common/Table.vue';
import Notice from '../components/common/Notice.vue';
const props=defineProps<{snapshots:Snapshot[];features:Record<string,string>;user:User;reload:()=>Promise<any>}>();
const utterances=ref<Utterance[]>([]),showImport=ref(false),mode=ref<'single'|'batch'>('single');
const name=ref(''),kind=ref('candidate'),raw=ref('');
const uploads=ref<UploadSession[]>([]),batchSize=ref(5000),uploading=ref(false),progress=ref({done:0,total:0,rows:0,name:''});
const {task,act}=useAsync();let timer:any;
async function reloadUploads(){try{uploads.value=await snapshotApi.uploads()}catch{/* 静默，主列表错误由全局提示 */}}
onMounted(reloadUploads);onUnmounted(()=>clearInterval(timer));
function startPoll(){clearInterval(timer);timer=setInterval(reloadUploads,2000)}
async function submitSingle(){await act(async()=>{await snapshotApi.create({name:name.value,kind:kind.value,filename:name.value+'.json',description:'研究员导入的匿名元特征',utterances:JSON.parse(raw.value)});await props.reload();showImport.value=false},'快照已导入')}
function splitBatches(rows:any[]){const out:any[][]=[];for(let i=0;i<rows.length;i+=batchSize.value)out.push(rows.slice(i,i+batchSize.value));return out}
async function submitBatch(){
 if(uploading.value)return;
 await act(async()=>{
  const rows=JSON.parse(raw.value);
  if(!Array.isArray(rows)||!rows.length)throw new Error('请提供片段JSON数组');
  const batches=splitBatches(rows);
  const session=await snapshotApi.initUpload({name:name.value,kind:kind.value,filename:name.value+'.json',description:'分批导入的匿名元特征',total_batches:batches.length});
  uploading.value=true;progress.value={done:0,total:batches.length,rows:0,name:name.value};startPoll();
  try{
   for(let i=0;i<batches.length;i++){
    const u=await snapshotApi.uploadBatch(session.id,{batch_no:i+1,utterances:batches[i]});
    progress.value={done:u.received_batches,total:u.total_batches,rows:u.row_count,name:name.value};
   }
   await snapshotApi.publishUpload(session.id,batches.length);
   await reloadUploads();await props.reload();
   showImport.value=false;task.message='全部批次已发布，快照可参与比较';
  }finally{uploading.value=false;clearInterval(timer);reloadUploads()}
 },'');
}
async function publishAgain(u:UploadSession){await act(async()=>{await snapshotApi.publishUpload(u.id,u.total_batches);await reloadUploads();await props.reload()},'快照已发布')}
const pct=(u:UploadSession)=>u.total_batches?Math.round(u.received_batches/u.total_batches*100):0;
const statusLabel=computed(()=>({open:'上传中',published:'已发布',rejected:'已拒绝'} as Record<string,string>));
</script>
<template>
 <PageTitle index="02 / SOURCE MATERIAL" title="同一把标尺，始于两份来源。" description="匿名片段只保留时长、噪声、语速、语言与设备类别。缺失值进入独立桶，新类别与参考缺失分开计数。">
  <button v-if="user.role!=='viewer'" @click="showImport=!showImport">导入匿名快照 ↗</button>
 </PageTitle>
 <Notice :error="task.error" :message="task.message"/>
 <form class="editor" v-if="showImport" @submit.prevent="mode==='single'?submitSingle():submitBatch()">
  <h2>导入匿名片段元特征</h2>
  <div class="mode-switch" v-if="!uploading">
   <button type="button" :class="{primary:mode==='single'}" @click="mode='single'">小快照直传（20–5000条）</button>
   <button type="button" :class="{primary:mode==='batch'}" @click="mode='batch'">百万级分批上传</button>
  </div>
  <div class="form-grid">
   <label>快照名称<input v-model="name" maxlength="100" required :disabled="uploading"/></label>
   <label>快照用途<select v-model="kind" :disabled="uploading"><option value="candidate">候选新增</option><option value="reference">参考冻结</option></select></label>
  </div>
  <label v-if="mode==='batch'">每批片段数（浏览器按此切分，逐批上传）<input type="number" v-model.number="batchSize" min="100" max="5000" step="100" :disabled="uploading"/></label>
  <label>片段JSON<textarea v-model="raw" rows="7" required :disabled="uploading" placeholder='[{"reference":"NEW-001","features":{"duration":3.1,"noise":10,"token_rate":4.8,"language":"zh","device":"mobile"}},…]'/></label>
  <p>五项特征完整；值可为null，保留桶名__NEW__/__MISSING__不可用作真实类别。跨批重复编号将整份拒绝；未发布的快照不参与比较。</p>
  <div v-if="mode==='batch'&&uploading" class="upload-progress">
   <small>正在上传 {{progress.name}}：批次 {{progress.done}}/{{progress.total}} · 已收 {{progress.rows.toLocaleString()}} 片段</small>
   <div class="progress-track"><div class="progress-fill" :style="{width:(progress.total?progress.done/progress.total*100:0)+'%'}"/></div>
  </div>
  <button class="primary" :disabled="task.busy||uploading" type="submit">{{uploading?'上传中…':mode==='single'?'校验并导入':'分批上传并发布'}}</button>
 </form>
 <div class="section-title" v-if="uploads.length"><h2>分批上传会话</h2><small>展示进度、发布结果与拒绝原因</small></div>
 <Table v-if="uploads.length" :headers="['会话 / 快照','用途','状态','批次进度','片段数','操作 / 原因']">
  <tr v-for="u in uploads" :key="u.id">
   <td><small>#{{u.id}}</small><strong>{{u.name}}</strong><small>{{u.filename}}</small></td>
   <td>{{u.kind==='reference'?'参考冻结':'候选新增'}}</td>
   <td><span class="upload-status" :class="u.status">{{statusLabel[u.status]||u.status}}</span></td>
   <td><div class="progress-track compact"><div class="progress-fill" :style="{width:pct(u)+'%'}"/></div><small>{{u.received_batches}}/{{u.total_batches}} 批 · {{pct(u)}}%</small></td>
   <td class="number">{{u.row_count.toLocaleString()}}</td>
   <td>
    <button v-if="u.status==='open'&&user.role!=='viewer'" class="text-button" @click="publishAgain(u)">发布 ↗</button>
    <small v-else-if="u.status==='rejected'" class="reject-reason">{{u.error}}</small>
    <small v-else>已进入快照列表</small>
   </td>
  </tr>
 </Table>
 <div class="section-title"><h2>可用快照</h2><small>仅已发布（传完）快照参与比较</small></div>
 <Table :headers="['快照 / 源文件','用途','片段数','说明','']">
  <tr v-for="s in snapshots" :key="s.id">
   <td><strong>{{s.name}}</strong><small>{{s.filename}}</small></td>
   <td>{{s.kind==='reference'?'参考冻结':'候选新增'}}</td>
   <td class="number">{{s.count}}</td>
   <td class="description-cell">{{s.description}}</td>
   <td><button class="text-button" @click="act(async()=>utterances=await snapshotApi.utterances(s.id),'')">查看前100片段 ↗</button></td>
  </tr>
 </Table>
 <div class="source-principle"><b>来源检查</b><p>参考箱只从参考样本拟合；候选的极端值进入冻结尾箱，不改变边界。类别参考未出现的值进入新类别桶，null进入缺失桶。KS仅比较连续特征非缺失值，缺失比例由箱计数另行呈现。</p></div>
 <template v-if="utterances.length">
  <div class="section-title"><h2>匿名元特征样本</h2><small>展示前{{utterances.length}}条；计算使用全量切片</small></div>
  <Table :headers="['片段',...Object.values(features)]"><tr v-for="u in utterances" :key="u.id"><td class="mono">{{u.reference}}</td><td v-for="key in Object.keys(features)" :key="key">{{u.features[key]??'缺失'}}</td></tr></Table>
 </template>
</template>
