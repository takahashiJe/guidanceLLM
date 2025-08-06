# backend/api_gateway/app/api/v1/chat.py

from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form
from typing import Optional
import uuid

# sharedディレクトリからCeleryタスクの「名前」をインポート
# (注: ここではまだ実装されていないが、先に定義しておく)
# from backend.shared.app.tasks import process_chat_message_task, start_navigation_task, update_location_task

# sharedディレクトリと、同じ階層のsecurity.pyからインポート
from backend.shared.app import models, schemas
from backend.api_gateway.app.security import get_current_user

router = APIRouter()

# --- ダミーのCeleryタスク（shared/app/tasks.pyにあると仮定） ---
# 実際のCeleryタスクが定義されるまでのプレースホルダー
class DummyTask:
    def delay(self, *args, **kwargs):
        print(f"Dummy task called with args: {args}, kwargs: {kwargs}")
        return "dummy_task_id"

process_chat_message_task = DummyTask()
start_navigation_task = DummyTask()
update_location_task = DummyTask()
# --- ダミーここまで ---


@router.post("/message", status_code=status.HTTP_202_ACCEPTED)
def post_message(
    session_id: uuid.UUID = Form(...),
    text: Optional[str] = Form(None),
    audio_file: Optional[UploadFile] = File(None),
    current_user: models.User = Depends(get_current_user)
):
    """
    FR-2, FR-7: ユーザーからのメッセージ（テキストor音声）を受け付け、
    Workerに対話処理タスクを投入する
    """
    if not text and not audio_file:
        raise HTTPException(status_code=400, detail="Either text or audio_file must be provided")

    # TODO: 音声ファイルが来た場合、S3などのストレージに保存し、そのURLをタスクに渡すのが望ましい
    audio_file_content = audio_file.file.read() if audio_file else None
    
    # Celeryタスクを非同期で呼び出す
    process_chat_message_task.delay(
        session_id=str(session_id),
        user_id=current_user.user_id,
        text=text,
        audio_data=audio_file_content # 本番ではURLやファイルパスを渡す
    )
    
    return {"message": "Request accepted, processing in background."}


@router.post("/navigation/start", status_code=status.HTTP_202_ACCEPTED)
def start_navigation(
    plan_id: int = Form(...),
    current_user: models.User = Depends(get_current_user)
):
    """
    FR-5: ナビゲーション開始のトリガー
    """
    # TODO: plan_idが本当にこのユーザーのものかDBで検証する
    start_navigation_task.delay(plan_id=plan_id, user_id=current_user.user_id)
    return {"message": "Navigation start request accepted."}


@router.post("/navigation/location", status_code=status.HTTP_202_ACCEPTED)
def update_location(
    location_data: schemas.LocationData, # Pydanticモデルでデータを受け取る
    current_user: models.User = Depends(get_current_user)
):
    """
    FR-5-3: ナビ中の位置情報更新
    """
    update_location_task.delay(
        session_id=str(location_data.session_id),
        user_id=current_user.user_id,
        latitude=location_data.latitude,
        longitude=location_data.longitude
    )
    return {"message": "Location update accepted."}
