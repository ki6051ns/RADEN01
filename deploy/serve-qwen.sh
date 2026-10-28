#!/usr/bin/env bash
set -euo pipefail
# Model weights must be provisioned on the GPU host or downloaded with its allowed network.
# Bind locally. Put authenticated TLS ingress in front of any remote endpoint.
args=(serve "${QWEN_MODEL_PATH:-Qwen/Qwen3-8B}"
  --served-model-name Qwen/Qwen3-8B
  --host 127.0.0.1 --port "${QWEN_PORT:-8000}"
  --dtype auto --max-model-len "${QWEN_MAX_MODEL_LEN:-8192}"
  --tensor-parallel-size "${QWEN_TENSOR_PARALLEL_SIZE:-1}"
  --gpu-memory-utilization "${QWEN_GPU_MEMORY_UTILIZATION:-0.90}")
# vLLM also reads VLLM_API_KEY directly; never echo or commit it.
exec vllm "${args[@]}"
