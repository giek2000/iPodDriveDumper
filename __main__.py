try:
    from .app import main
except ImportError:
    from iPodRawDriveDumper.app import main

raise SystemExit(main())
