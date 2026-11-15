from fastapi import APIRouter,Depends
from sqlalchemy.orm import Session
from src.config.database import get_db
from src.controllers.dependencies import current_user,writers
from src.schemas.snapshot import SnapshotInput,UploadInitInput,UploadBatchInput,UploadPublishInput
from src.services import snapshots
from src.constants.features import NUMERIC,CATEGORICAL
router=APIRouter()
@router.get('/features')
def features(user=Depends(current_user)):return {'numeric':NUMERIC,'categorical':CATEGORICAL}
@router.get('/snapshots')
def index(db:Session=Depends(get_db),user=Depends(current_user)):return snapshots.list_snapshots(db)
@router.post('/snapshots',status_code=201)
def create(data:SnapshotInput,db:Session=Depends(get_db),user=Depends(writers)):return snapshots.create_snapshot(db,data,user)
@router.get('/snapshots/{id}/utterances')
def utterances(id:int,db:Session=Depends(get_db),user=Depends(current_user)):return snapshots.get_utterances(db,id,100)
@router.get('/uploads')
def uploads(db:Session=Depends(get_db),user=Depends(current_user)):return snapshots.list_uploads(db,user)
@router.post('/uploads',status_code=201)
def init_upload(data:UploadInitInput,db:Session=Depends(get_db),user=Depends(writers)):return snapshots.init_upload(db,data,user)
@router.get('/uploads/{upload_id}')
def upload_detail(upload_id:int,db:Session=Depends(get_db),user=Depends(current_user)):return snapshots.get_upload(db,upload_id,user)
@router.post('/uploads/{upload_id}/batches',status_code=201)
def add_batch(upload_id:int,data:UploadBatchInput,db:Session=Depends(get_db),user=Depends(writers)):return snapshots.add_batch(db,upload_id,data,user)
@router.post('/uploads/{upload_id}/publish')
def publish(upload_id:int,data:UploadPublishInput,db:Session=Depends(get_db),user=Depends(writers)):return snapshots.publish_upload(db,upload_id,data,user)
