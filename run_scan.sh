#!/bin/bash
# run_scan.sh — Start Celery worker, dispatch scan_urls tasks, wait for completion
#
# Usage (from WSL or bash):
#   ./run_scan.sh
#
# This script:
#   1. Activates the venv
#   2. Starts a Celery worker in the background
#   3. Waits for the worker to be ready
#   4. Dispatches scan_urls tasks for linkedin + naukri (filtered)
#   5. Monitors the worker — once it goes idle (no active/reserved tasks), shuts down

set -e

cd "$(dirname "$0")"

# Activate virtualenv
source venv12/bin/activate

# Start Celery worker in the background, logging to file and stdout
echo "=== Starting Celery worker ==="
celery -A JobTracker worker -l INFO &
CELERY_PID=$!

# Give the worker time to boot and connect to the broker
echo "=== Waiting for Celery worker to be ready ==="
MAX_WAIT=30
WAITED=0
while ! celery -A JobTracker inspect ping --timeout 5 &>/dev/null; do
    sleep 2
    WAITED=$((WAITED + 2))
    if [ $WAITED -ge $MAX_WAIT ]; then
        echo "ERROR: Celery worker did not start within ${MAX_WAIT}s"
        kill $CELERY_PID 2>/dev/null
        exit 1
    fi
    echo "  ...waiting (${WAITED}s)"
done
echo "=== Celery worker is ready ==="

# Dispatch the scan_urls tasks to the worker
echo "=== Dispatching scan_urls tasks ==="
python manage.py job_pipeline -s linkedin naukri -t filtered

# Monitor: wait until worker has no active or reserved tasks, then shut down
echo "=== Waiting for tasks to complete ==="
sleep 10  # Let tasks get picked up first

while true; do
    # Get active + reserved task counts from inspect
    ACTIVE=$(celery -A JobTracker inspect active --timeout 10 2>/dev/null \
        | grep -c "job.tasks" || true)
    RESERVED=$(celery -A JobTracker inspect reserved --timeout 10 2>/dev/null \
        | grep -c "job.tasks" || true)

    TOTAL=$((ACTIVE + RESERVED))

    if [ "$TOTAL" -eq 0 ]; then
        echo "=== All tasks completed ==="
        break
    fi

    echo "  ...${ACTIVE} active, ${RESERVED} reserved tasks remaining"
    sleep 15
done

# Gracefully shut down the worker
echo "=== Shutting down Celery worker ==="
kill $CELERY_PID 2>/dev/null
wait $CELERY_PID 2>/dev/null || true

echo "=== Done ==="
