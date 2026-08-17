#!/usr/bin/env python3
import os
import sys

# Add current directory to sys.path so it can find the 'search' package
sys.path.append(os.path.dirname(__file__))

from search.cli import main

if __name__ == "__main__":
    main()
