"""PyInstaller entry point for the bundled Champ Studio engine binary."""

import multiprocessing

from champstudio.main import run

if __name__ == "__main__":
    multiprocessing.freeze_support()
    run()
