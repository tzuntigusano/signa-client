"""Lanzador de Signa sin consola (lo usa el arranque con Windows): python -m signa.app desde cualquier carpeta."""
import os
import runpy
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
runpy.run_module("signa.app", run_name="__main__", alter_sys=True)
