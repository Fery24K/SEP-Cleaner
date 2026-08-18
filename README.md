# Build Linux executable (single-file) for SEP_Cleaner

Two recommended ways to build a Linux single-file executable from this repo while on Windows:

- Use Docker (recommended for reproducible builds)
- Use WSL2 (if you prefer building directly inside a Linux environment)

Docker (build here):

```bash
docker build -t sep-cleaner-build .
# then extract built artifacts to ./dist
container=$(docker create sep-cleaner-build)
docker cp $container:/app/dist ./dist
docker rm $container
```

Or simply run the included helper script (requires Docker installed):

```bash
./build_linux.sh
```

Notes:

- PyInstaller must run on the target OS; this Dockerfile runs PyInstaller inside a Linux container so the produced binary is Linux ELF.
- `--add-data` uses `:` on Linux (Dockerfile uses `logo.png:.` and `icons:icons`).
- If you only have `logo.ico`, replace `--icon` with `logo.ico` but icons on Linux often use PNG; adjust as needed.

Want me to run the Docker build here now (if Docker is available), or create a WSL script instead? Reply which you prefer.
