#!/usr/bin/env python3
"""Small standalone helper that opens a tkinter file dialog and prints JSON result.

This script is intentionally minimal and runs in its own process so crashes here
won't affect the main application.
"""
import sys
import json
import argparse
try:
    import tkinter as tk
    from tkinter import filedialog
except Exception:
    # If tkinter cannot be imported, exit with error
    sys.exit(2)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=['open','open_multi','save','dir'], required=True)
    parser.add_argument('--title', default='')
    parser.add_argument('--initialdir', default=None)
    parser.add_argument('--initialfile', default=None)
    parser.add_argument('--defaultextension', default=None)
    parser.add_argument('--filetypes', default=None)  # JSON string of list of tuples
    args = parser.parse_args()

    root = tk.Tk()
    root.withdraw()

    opts = {}
    if args.title:
        opts['title'] = args.title
    if args.initialdir:
        opts['initialdir'] = args.initialdir
    if args.initialfile:
        opts['initialfile'] = args.initialfile
    if args.defaultextension:
        opts['defaultextension'] = args.defaultextension
    if args.filetypes:
        try:
            opts['filetypes'] = json.loads(args.filetypes)
        except Exception:
            pass

    try:
        if args.mode == 'open':
            path = filedialog.askopenfilename(**opts)
            if not path:
                sys.exit(1)
            print(json.dumps({'path': path}))
        elif args.mode == 'open_multi':
            paths = filedialog.askopenfilenames(**opts)
            if not paths:
                sys.exit(1)
            print(json.dumps({'paths': list(paths)}))
        elif args.mode == 'save':
            path = filedialog.asksaveasfilename(**opts)
            if not path:
                sys.exit(1)
            print(json.dumps({'path': path}))
        elif args.mode == 'dir':
            path = filedialog.askdirectory(**opts)
            if not path:
                sys.exit(1)
            print(json.dumps({'path': path}))
    except Exception:
        # Dialog crashed for some reason
        sys.exit(3)
    finally:
        try:
            root.destroy()
        except Exception:
            pass


if __name__ == '__main__':
    main()
