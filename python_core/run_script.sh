#!/bin/bash
# 通用后台执行脚本
# 使用方式：bash run_script.sh 脚本目录 脚本文件名 [额外参数...]
# 示例：bash run_script.sh src data_fetcher.py
#       bash run_script.sh src sample_generator.py --workers 2

# 校验入参数量
if [ $# -lt 2 ]; then
    echo "参数错误！至少需要2个参数："
    echo "用法：$0 脚本目录 脚本文件名 [额外参数...]"
    echo "示例：$0 src data_fetcher.py"
    echo "      $0 src sample_generator.py --workers 2"
    exit 1
fi

# 接收入参
SCRIPT_DIR="$1"
SCRIPT_NAME="$2"
shift 2  # 移除前两个参数，剩余为额外参数
EXTRA_ARGS="$@"

# 拼接完整脚本路径
FULL_SCRIPT_PATH="${SCRIPT_DIR}/${SCRIPT_NAME}"
# 日志文件名：脚本名去掉后缀.log
LOG_NAME="${SCRIPT_NAME%.*}.log"

# 校验脚本文件是否存在
if [ ! -f "${FULL_SCRIPT_PATH}" ]; then
    echo "错误：脚本文件不存在 -> ${FULL_SCRIPT_PATH}"
    exit 1
fi

# 后台执行，日志输出到当前目录
nohup python "${FULL_SCRIPT_PATH}" ${EXTRA_ARGS} > "${LOG_NAME}" 2>&1 &
PID=$!

echo "========================================"
echo "脚本后台启动成功"
echo "执行脚本：${FULL_SCRIPT_PATH} ${EXTRA_ARGS}"
echo "进程PID：${PID}"
echo "日志文件：${LOG_NAME}"
echo "实时查看日志命令：tail -f ${LOG_NAME}"
echo "========================================"
