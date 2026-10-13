"""Launch the dashboard by default; benchmark flags retain the command-line runner."""

import sys

if __name__ == "__main__":
    if len(sys.argv) == 1 or sys.argv[1] == "--web":
        from decision_tests.web import main

        main(sys.argv[2:] if len(sys.argv) > 1 else [])
    else:
        from decision_tests.cli import main

        main()
