import sys

def print_progress(line: str):
    """
    Print progress tracking information.
    If running in a terminal, uses carriage return (\r) for a single-line loading bar.
    If running in a background worker (like Celery), uses standard print() to avoid log pollution.
    """
    print(line)
    # if sys.stdout.isatty():
    #     print(f"\r{line}".ljust(180), end='', flush=True)
    # else:
    #     print(line)
