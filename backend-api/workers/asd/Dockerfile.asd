# Active Speaker Detection worker — Batch/SageMaker 影像（延後啟用）
#
# 這是接縫佔位：MVP 走 app 內的 HeuristicASD（純函式代理），無需此映像。
# 要換上真 ASD 模型（TalkNet / Light-ASD）時：
#   1. 於此安裝 ffmpeg（抽音訊/取幀）+ torch + 模型權重
#   2. entrypoint 讀 {video, face tracks, diarization segments} → 產 asd_result.v1
#   3. 以 workers.asd.worker.build_asd_result 驗證輸出、寫回 work bucket
#   4. Fusion 不需改（DIP：只認 asd_result.v1）
#
# 部署：ECR 映像 + AWS Batch（EC2, On-Demand+Spot）；由 Step Functions 呼叫。
FROM public.ecr.aws/docker/library/python:3.12-slim

# ffmpeg 供抽音訊/取樣影格（真模型需要；heuristic 代理不需要）
# RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY backend-api/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
# TODO(real-model): pip install torch 及 ASD 模型相依、COPY 權重

COPY backend-api /app
COPY contracts /app/contracts

# entrypoint 佔位；真模型上線時替換為 batch 進入點腳本
CMD ["python", "-c", "print('ASD worker image — replace CMD with real batch entrypoint')"]
