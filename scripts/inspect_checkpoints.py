"""
Diagnostic CLI tool to inspect and manage LangGraph checkpoints in apply_checkpoints.db.

Usage:
    # 1. Summary of all application runs in the checkpoint DB:
    python scripts/inspect_checkpoints.py

    # 2. Inspect latest state of a specific run:
    python scripts/inspect_checkpoints.py --run 2
    python scripts/inspect_checkpoints.py --thread apply-2

    # 3. View step-by-step history across all checkpoints of a run:
    python scripts/inspect_checkpoints.py --run 2 --history

    # 4. View latest state as formatted JSON:
    python scripts/inspect_checkpoints.py --run 2 --latest

    # 5. Clear checkpoints (all or specific run):
    python scripts/inspect_checkpoints.py --clear
    python scripts/inspect_checkpoints.py --run 2 --clear

    # 6. Inspect raw SQLite tables and schema:
    python scripts/inspect_checkpoints.py --raw
"""
import os
import sys
import json
import sqlite3
import argparse
from typing import Optional, Any

# Ensure project root is in sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

DEFAULT_DB_PATH = os.path.join(PROJECT_ROOT, 'apply_checkpoints.db')


def setup_django_if_possible():
    """Attempt to setup Django for ApplicationRun / Job metadata enrichment."""
    try:
        os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'JobTracker.settings')
        import django
        django.setup()
        return True
    except Exception:
        return False


def get_sqlite_conn(db_path: str):
    """Open SQLite connection."""
    if not os.path.exists(db_path):
        print(f"Error: Database file not found at {db_path}")
        sys.exit(1)
    return sqlite3.connect(db_path)


def print_header(title: str, width: int = 80):
    print("=" * width)
    print(f"  {title}")
    print("=" * width)


def print_section(title: str, width: int = 80):
    print("\n" + "-" * width)
    print(f"  {title}")
    print("-" * width)


def get_checkpointer_instance(db_path: str):
    """Load LangGraph SqliteSaver checkpointer."""
    try:
        from langgraph.checkpoint.sqlite import SqliteSaver
        conn = sqlite3.connect(db_path, check_same_thread=False)
        return SqliteSaver(conn)
    except ImportError:
        return None


def get_all_threads(db_path: str) -> list[str]:
    """Retrieve distinct thread IDs from SQLite checkpoints."""
    conn = get_sqlite_conn(db_path)
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT DISTINCT thread_id FROM checkpoints ORDER BY thread_id;")
        threads = [r[0] for r in cursor.fetchall()]
    except sqlite3.OperationalError:
        threads = []
    finally:
        conn.close()
    return threads


def list_summary(db_path: str):
    """List high-level summary of all runs in apply_checkpoints.db."""
    print_header(f"APPLY CHECKPOINTS SUMMARY — {os.path.basename(db_path)}")

    threads = get_all_threads(db_path)
    if not threads:
        print("No threads or checkpoints found in database.")
        return

    has_django = setup_django_if_possible()
    ApplicationRun = None
    if has_django:
        try:
            from job.models import ApplicationRun
        except Exception:
            pass

    saver = get_checkpointer_instance(db_path)

    print(f"{'Thread ID':<16} | {'Run #':<6} | {'Status':<16} | {'Step':<5} | {'Checkpoints':<11} | {'Job Info':<30}")
    print("-" * 95)

    for thread_id in threads:
        run_num = thread_id.replace("apply-", "") if thread_id.startswith("apply-") else "-"
        
        # Count checkpoints in DB
        conn = get_sqlite_conn(db_path)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM checkpoints WHERE thread_id = ?", (thread_id,))
        cp_count = cur.fetchone()[0]
        conn.close()

        status = "-"
        step = "-"
        job_info = "-"

        # Get latest state if saver is available
        if saver:
            config = {"configurable": {"thread_id": thread_id}}
            tuple_obj = saver.get_tuple(config)
            if tuple_obj and tuple_obj.checkpoint:
                channel_values = tuple_obj.checkpoint.get("channel_values", {})
                status = channel_values.get("status", "-")
                step = str(channel_values.get("step_number", "-"))

        # Enrich with Django DB if available
        if ApplicationRun and run_num.isdigit():
            try:
                run = ApplicationRun.objects.select_related('job').get(id=int(run_num))
                if status == "-":
                    status = run.status
                job_info = f"{run.job.company} — {run.job.position}"[:30]
            except Exception:
                pass

        print(f"{thread_id:<16} | {run_num:<6} | {status:<16} | {step:<5} | {cp_count:<11} | {job_info:<30}")

    print("\nTip: Run with '--run <ID>' to see details, or '--clear' to wipe.")


def sanitize_state(state: dict, include_dom: bool = False) -> dict:
    """Format state dictionary for clean CLI viewing, truncating large blobs."""
    clean = {}
    for k, v in state.items():
        if k == 'dom_snapshot' and not include_dom:
            if v:
                clean[k] = f"<HTML string: {len(v)} chars (use --include-dom to view)>"
            else:
                clean[k] = None
        elif k == 'screenshot_path' and v:
            clean[k] = v
        else:
            clean[k] = v
    return clean


def inspect_thread(db_path: str, thread_id: str, show_history: bool = False, show_latest_json: bool = False, include_dom: bool = False):
    """Detailed inspection of a specific thread."""
    print_header(f"INSPECTING THREAD: {thread_id}")

    saver = get_checkpointer_instance(db_path)
    if not saver:
        print("LangGraph SqliteSaver is not available.")
        return

    config = {"configurable": {"thread_id": thread_id}}
    latest_tuple = saver.get_tuple(config)

    if not latest_tuple:
        print(f"No checkpoint found for thread '{thread_id}'.")
        return

    latest_state = latest_tuple.checkpoint.get("channel_values", {})

    # Django enrichment
    run_num = thread_id.replace("apply-", "") if thread_id.startswith("apply-") else None
    if setup_django_if_possible() and run_num and run_num.isdigit():
        try:
            from job.models import ApplicationRun
            run = ApplicationRun.objects.select_related('job').get(id=int(run_num))
            print(f"  Job:             {run.job.position} at {run.job.company}")
            print(f"  Job URL:         {run.job.apply_url}")
            print(f"  Run Status (DB): {run.status}")
            if run.skip_reason:
                print(f"  Skip Reason:     {run.skip_reason}")
            if run.escalation_reason:
                print(f"  Escalation:      {run.escalation_reason}")
        except Exception:
            pass

    print_section("LATEST CHECKPOINT SUMMARY")
    print(f"  Checkpoint ID:     {latest_tuple.checkpoint.get('id')}")
    print(f"  Timestamp:         {latest_tuple.checkpoint.get('ts')}")
    print(f"  Graph Status:      {latest_state.get('status')}")
    print(f"  Current Step:      {latest_state.get('step_number')}")
    print(f"  Page Type:         {latest_state.get('page_type')}")
    print(f"  Current URL:       {latest_state.get('current_url')}")
    print(f"  Apply Type:        {latest_state.get('apply_type')}")
    print(f"  Fresh Decisions:   {latest_state.get('had_fresh_decisions')} (LLM used on cache miss)")
    print(f"  Action Failures:   {latest_state.get('consecutive_action_failures')} consecutive")
    print(f"  Total Actions:     {latest_state.get('total_actions_executed')}")
    print(f"  Last Action Ok:    {latest_state.get('last_action_success')}")
    if latest_state.get('last_action_error'):
        print(f"  Last Error:        {latest_state.get('last_action_error')}")
    if latest_state.get('screenshot_path'):
        print(f"  Screenshot:        {latest_state.get('screenshot_path')}")

    checklist = latest_state.get('checklist', [])
    chk_idx = latest_state.get('checklist_index', 0)
    print(f"\n  Checklist ({len(checklist)} items, next index: {chk_idx}):")
    for i, item in enumerate(checklist):
        marker = "👉" if i == chk_idx else "  "
        action = item.get('action', item.get('action_type', ''))
        target = item.get('target', item.get('target_label', ''))
        purpose = item.get('purpose', '')
        print(f"    {marker} [{i}] {action} → {target} ({purpose})")

    if show_latest_json:
        print_section("LATEST STATE JSON")
        clean_state = sanitize_state(latest_state, include_dom)
        print(json.dumps(clean_state, indent=2, default=str))

    if show_history:
        print_section("CHECKPOINT TIMELINE / HISTORY")
        checkpoints_list = list(saver.list(config))
        checkpoints_list.reverse()

        print(f"Total Checkpoint States Recorded: {len(checkpoints_list)}\n")
        for i, cp in enumerate(checkpoints_list, 1):
            cp_ts = cp.checkpoint.get("ts")
            meta = cp.metadata or {}
            source_node = meta.get("source", "")
            writes = meta.get("writes") or {}
            writing_nodes = list(writes.keys()) if isinstance(writes, dict) else []
            node_str = ", ".join(writing_nodes) if writing_nodes else source_node

            st = cp.checkpoint.get("channel_values", {})
            st_step = st.get("step_number")
            st_ptype = st.get("page_type")
            st_status = st.get("status")
            st_last_succ = st.get("last_action_success")

            print(f"[{i:02d}] {cp_ts} | Node(s): {node_str:<18} | Status: {st_status:<14} | Step: {str(st_step):<3} | Page: {str(st_ptype):<16} | Last Action Success: {str(st_last_succ)}")
            if st.get("last_action_error"):
                print(f"     ↳ Error: {st.get('last_action_error')}")


def clear_checkpoints(db_path: str, thread_id: Optional[str] = None):
    """Delete checkpoints for a specific thread or all threads."""
    conn = get_sqlite_conn(db_path)
    cur = conn.cursor()
    if thread_id:
        cur.execute("DELETE FROM checkpoints WHERE thread_id = ?", (thread_id,))
        cur.execute("DELETE FROM writes WHERE thread_id = ?", (thread_id,))
        conn.commit()
        print(f"Cleared checkpoints and writes for thread: {thread_id}")
    else:
        cur.execute("DELETE FROM checkpoints;")
        cur.execute("DELETE FROM writes;")
        conn.commit()
        print("Cleared ALL checkpoints and writes from database.")
    conn.close()


def inspect_raw_tables(db_path: str):
    """Inspect low-level SQLite tables, schemas, and record counts."""
    print_header(f"RAW SQLITE INSPECTION: {os.path.basename(db_path)}")
    conn = get_sqlite_conn(db_path)
    cursor = conn.cursor()

    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = [row[0] for row in cursor.fetchall()]

    if not tables:
        print("No tables found in this SQLite database.")
        return

    for table in tables:
        print_section(f"Table: {table}")
        cursor.execute(f"PRAGMA table_info({table});")
        cols = cursor.fetchall()
        print("Columns:")
        for col in cols:
            cid, name, col_type, notnull, dflt_value, pk = col
            pk_marker = " [PRIMARY KEY]" if pk else ""
            print(f"  - {name} ({col_type}){pk_marker}")

        cursor.execute(f"SELECT COUNT(*) FROM {table};")
        count = cursor.fetchone()[0]
        print(f"Total Rows: {count}")

    conn.close()


def main():
    parser = argparse.ArgumentParser(
        description="Inspect and manage LangGraph checkpoints stored in apply_checkpoints.db."
    )
    parser.add_argument(
        "--db",
        default=DEFAULT_DB_PATH,
        help=f"Path to SQLite database file (default: {DEFAULT_DB_PATH})"
    )
    parser.add_argument(
        "-r", "--run",
        type=int,
        help="ApplicationRun ID to inspect (converts to thread 'apply-<ID>')"
    )
    parser.add_argument(
        "-t", "--thread",
        type=str,
        help="Thread ID to inspect (e.g. 'apply-2')"
    )
    parser.add_argument(
        "-H", "--history",
        action="store_true",
        help="Display step-by-step checkpoint history timeline"
    )
    parser.add_argument(
        "-l", "--latest",
        action="store_true",
        help="Output full latest state as formatted JSON"
    )
    parser.add_argument(
        "--include-dom",
        action="store_true",
        help="Include full HTML dom_snapshot without truncation"
    )
    parser.add_argument(
        "--clear",
        action="store_true",
        help="Clear checkpoints from database (all threads or specified run)"
    )
    parser.add_argument(
        "--raw",
        action="store_true",
        help="Inspect low-level raw SQLite table schema and counts"
    )

    args = parser.parse_args()

    if args.raw:
        inspect_raw_tables(args.db)
        return

    target_thread = None
    if args.run is not None:
        target_thread = f"apply-{args.run}"
    elif args.thread:
        target_thread = args.thread

    if args.clear:
        clear_checkpoints(args.db, target_thread)
        return

    if target_thread:
        inspect_thread(
            db_path=args.db,
            thread_id=target_thread,
            show_history=args.history,
            show_latest_json=args.latest,
            include_dom=args.include_dom
        )
    else:
        list_summary(args.db)


if __name__ == "__main__":
    main()
