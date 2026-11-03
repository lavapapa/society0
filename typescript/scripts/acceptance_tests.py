"""安装态、性能与真实端点清单；acceptance.py、test-build.py、acceptance_ledger.py 共用，无副作用。"""
INSTALLED_ONLY = {'consumer.test.mjs', 'peer-contract.test.mjs', 'preflight-session.test.mjs', 'cli.test.mjs', 'module-sidecars.test.mjs'}
INSTALLED_SCRIPTS_PREFIX = 'pr12-'
INSTALLED_PY = {'host-quality-consumer.py', 'artifacts.test.py'}
INSTALLED_COPIES = {
    'pr02.test.mjs': {'../.build/host/dist/index.js': '@society0/host'},
    'exports.test.mjs': {'../.build/host/dist/index.js': '@society0/host'},
    'pr03-config.test.mjs': {'../.build/host/dist/profile-loader/compiler.js': '@society0/host/profile-loader', '../.build/host/dist/abi.js': '@society0/host/abi', '../.build/host/dist/config-resolvers/index.js': '@society0/host/config-resolvers', '../.build/schemastery/dist/index.js': '@society0/schemastery'},
    'pr04.test.mjs': {'../.build/core/dist/sqlite/database.js': '@society0/core/sqlite/database', '../.build/core/dist/execution-fence/state.js': '@society0/core/execution-fence/state'},
    'execution-fence.test.mjs': {'../.build/core/dist/execution-fence/state.js': '@society0/core/execution-fence/state'},
    'pr04-cordis.test.mjs': 'regex-public',
    'pr04-directory.test.mjs': 'regex-public',
    'pr11-random.test.mjs': 'random-native',
    'pr11-random-bootstrap.test.mjs': 'random-native',
    'pr11-random-order.test.mjs': 'random-native',
}
# 需要已安装 @society0/host 才能解析第三方包 peer 的测试；在 build 层运行会写共享 node_modules。
BUILD_EXCLUDED = {'exports.test.mjs'}
LIVE = {'pr08-real.mjs', 'rv3-04-litellm.mjs'}
PERFORMANCE = {'pr05-scale.test.mjs'}

INSTALLED_SUPPORT = {'pr07-support.mjs': 'regex-public'}
