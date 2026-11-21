FROM python:3.13.0-slim-bookworm
WORKDIR /bot
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY bot.py navigation.py mechfight.py tactics.py online_rl.py strategy.json ./
USER 65534:65534
ENTRYPOINT ["python3", "-u", "/bot/bot.py"]
