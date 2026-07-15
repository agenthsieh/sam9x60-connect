FROM python:3.12-slim

WORKDIR /app
# --chmod so the file is world-readable regardless of the build context's mode
# (files on an SMB/NAS share are often 0700) and readable by the non-root USER.
COPY --chmod=0644 app.py .

# Run as a non-root UID. We use a bare numeric UID rather than `useradd` so the
# image builds with no RUN step — Synology's kernel rejects the seccomp profile
# BuildKit applies to RUN, and the app needs no /etc/passwd entry or home dir.
USER 10001:10001

EXPOSE 8091
HEALTHCHECK --interval=30s --timeout=4s --start-period=5s \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8091/healthz',timeout=3).status==200 else 1)"]

CMD ["python", "app.py"]
