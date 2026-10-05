#!/usr/bin/env bash
set -eu
TASK=/mnt/data/l20/qin/society0-filesystem-final-20261005
cd "$TASK"
tar -xf source.tar -C source
/root/miniforge3/bin/python3 -m venv env
# 复制已验收的固定依赖字节到独立环境；当前产品单独构建和安装。
ENVLIB="$TASK/env/lib/python3.12/site-packages"
cp -a /tmp/society0-acceptance-20261004/all/lib/python3.12/site-packages/. "$ENVLIB/"
find "$ENVLIB" -maxdepth 1 -name 'society0*' -exec rm -rf {} +
source /mnt/data/l20/qin/.tailcat/home/.config/tailcat/proxy.env
export CARGO_HOME=/tmp/society0-core-native-build-20261004/cargo
export RUSTUP_HOME=/tmp/society0-core-native-build-20261004/rustup
export RUSTUP_TOOLCHAIN=1.95.0
export PATH="$CARGO_HOME/bin:$PATH"
export CARGO_TARGET_DIR="$TASK/target"
cd "$TASK/source/native/society0-filesystem"
/tmp/society0-core-native-build-20261004/build-env/bin/maturin build --release --locked --out "$TASK/wheels" > "$TASK/build.txt" 2>&1
cd "$TASK/source"
"$TASK/env/bin/python" -m pip install --no-deps --ignore-installed "$TASK"/wheels/*.whl . > "$TASK/install.txt" 2>&1
"$TASK/env/bin/python" -m pytest -m 'not real_e2e' --junitxml="$TASK/full.xml" > "$TASK/full.txt" 2>&1
"$TASK/env/bin/python" -m pytest tests/experiments -m 'not real_e2e' --junitxml="$TASK/experiments.xml" > "$TASK/experiments.txt" 2>&1
cd "$TASK"
"$TASK/env/bin/python" -c 'import society0,society0_filesystem,platform; print(platform.platform()); print(society0.__file__); print(society0_filesystem.__file__); print("WHEEL_SMOKE_OK")' > "$TASK/wheel-smoke.txt"
printf 'COMPLETE\n' > "$TASK/result.txt"
