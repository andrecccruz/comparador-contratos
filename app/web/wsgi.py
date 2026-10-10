# -*- coding: utf-8 -*-
"""Ponto de entrada para Gunicorn na VPS (a fila e uma thread em memoria:
use UM worker com varias threads).

    gunicorn -w 1 --threads 8 -b 127.0.0.1:5000 wsgi:app
"""
from app import app, iniciar_servicos  # noqa: F401

iniciar_servicos()
