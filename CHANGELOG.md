# Changelog

## [1.5.0](https://github.com/GoogleCloudPlatform/alloydb-python-connector/compare/v1.4.0...v1.5.0) (2024-11-12)


### Features

* add support for Python 3.13 ([#383](https://github.com/GoogleCloudPlatform/alloydb-python-connector/issues/383)) ([22ef77c](https://github.com/GoogleCloudPlatform/alloydb-python-connector/commit/22ef77c939fe279d749b17a3aded2df3dd565dc6))
* drop support for Python 3.8 ([#386](https://github.com/GoogleCloudPlatform/alloydb-python-connector/issues/386)) ([41ae4e3](https://github.com/GoogleCloudPlatform/alloydb-python-connector/commit/41ae4e3e7fc409749b1379e316be6d3b4996cf1a))


### Documentation

* add steps for testing the connector ([#372](https://github.com/GoogleCloudPlatform/alloydb-python-connector/issues/372)) ([2035a11](https://github.com/GoogleCloudPlatform/alloydb-python-connector/commit/2035a11274293f4c51be53e8ea7de126fcebf757))
* make IP type defaults clear in README ([#369](https://github.com/GoogleCloudPlatform/alloydb-python-connector/issues/369)) ([1d8a9a6](https://github.com/GoogleCloudPlatform/alloydb-python-connector/commit/1d8a9a686a8917e2d74652718fbeb25716dbfe75)), closes [#368](https://github.com/GoogleCloudPlatform/alloydb-python-connector/issues/368)

## [1.4.0](https://github.com/GoogleCloudPlatform/alloydb-python-connector/compare/v1.3.0...v1.4.0) (2024-08-14)


### Features

* use non-blocking disk read/writes ([#360](https://github.com/GoogleCloudPlatform/alloydb-python-connector/issues/360)) ([ba434e7](https://github.com/GoogleCloudPlatform/alloydb-python-connector/commit/ba434e7a3792edec3509bb265d6e131e4316406b))

## [1.3.0](https://github.com/GoogleCloudPlatform/alloydb-python-connector/compare/v1.2.1...v1.3.0) (2024-07-25)

### Features

*   add standardized debug logging
    ([#354](https://github.com/GoogleCloudPlatform/alloydb-python-connector/issues/354))
    ([14d6b1c](https://github.com/GoogleCloudPlatform/alloydb-python-connector/commit/14d6b1c291766eddf9110927ad726892a0fe9798))

### Bug Fixes

*   remove default argument `now` from `_seconds_until_refresh`
    ([#356](https://github.com/GoogleCloudPlatform/alloydb-python-connector/issues/356))
    ([27f31cd](https://github.com/GoogleCloudPlatform/alloydb-python-connector/commit/27f31cdc73bb6f4c762e5d9776721a4ea5f2e256)),
    closes
    [#357](https://github.com/GoogleCloudPlatform/alloydb-python-connector/issues/357)

## [1.2.1](https://github.com/GoogleCloudPlatform/alloydb-python-connector/compare/v1.2.0...v1.2.1) (2024-07-22)

### Bug Fixes

*   refresh token prior to metadata exchange
    ([#351](https://github.com/GoogleCloudPlatform/alloydb-python-connector/issues/351))
    ([1ab11e6](https://github.com/GoogleCloudPlatform/alloydb-python-connector/commit/1ab11e68f5575d8d2195e2a78f595d779284fa8d)),
    closes
    [#346](https://github.com/GoogleCloudPlatform/alloydb-python-connector/issues/346)

## [1.2.0](https://github.com/GoogleCloudPlatform/alloydb-python-connector/compare/v1.1.2...v1.2.0) (2024-06-25)

### Features

*   add support for lazy refresh strategy
    ([#337](https://github.com/GoogleCloudPlatform/alloydb-python-connector/issues/337))
    ([fbf0179](https://github.com/GoogleCloudPlatform/alloydb-python-connector/commit/fbf017933dfa0297cd57eff31b9061135d9637f2))

## [1.1.2](https://github.com/GoogleCloudPlatform/alloydb-python-connector/compare/v1.1.1...v1.1.2) (2024-06-12)

### Bug Fixes

*   update dependencies to latest
    ([#327](https://github.com/GoogleCloudPlatform/alloydb-python-connector/issues/327))
    ([fac87f0](https://github.com/GoogleCloudPlatform/alloydb-python-connector/commit/fac87f0dc249a27510a43936bf7669bdd74b3322))

## [1.1.1](https://github.com/GoogleCloudPlatform/alloydb-python-connector/compare/v1.1.0...v1.1.1) (2024-05-14)

### Dependencies

*   update dependency aiohttp to v3.9.5
    ([#303](https://github.com/GoogleCloudPlatform/alloydb-python-connector/issues/303))
    ([213adbe](https://github.com/GoogleCloudPlatform/alloydb-python-connector/commit/213adbebb38da9c3d77493ae50640ee71c21cfd1))

## [1.1.0](https://github.com/GoogleCloudPlatform/alloydb-python-connector/compare/v1.0.0...v1.1.0) (2024-04-16)

### Features

*   add support for PSC
    ([#291](https://github.com/GoogleCloudPlatform/alloydb-python-connector/issues/291))
    ([9698431](https://github.com/GoogleCloudPlatform/alloydb-python-connector/commit/9698431993cbca1d63fe61090f44ec977bff4f8d))

### Dependencies

*   update dependency aiohttp to v3.9.4
    ([#299](https://github.com/GoogleCloudPlatform/alloydb-python-connector/issues/299))
    ([c3971bd](https://github.com/GoogleCloudPlatform/alloydb-python-connector/commit/c3971bd62b2390583378b6f976d31c1fcfdb39e7))
*   Update dependency google-auth to v2.29.0
    ([#281](https://github.com/GoogleCloudPlatform/alloydb-python-connector/issues/281))
    ([473f4ab](https://github.com/GoogleCloudPlatform/alloydb-python-connector/commit/473f4abbe8e7524c56bd530f13294283a6b4cbcf))
*   Update dependency pg8000 to v1.31.1
    ([#292](https://github.com/GoogleCloudPlatform/alloydb-python-connector/issues/292))
    ([d845ac3](https://github.com/GoogleCloudPlatform/alloydb-python-connector/commit/d845ac3ffe539f05fb058fec1ff175a08aa888a0))

## [1.0.0](https://github.com/GoogleCloudPlatform/alloydb-python-connector/compare/v0.4.1...v1.0.0) (2024-03-12)

### Features

*   support `ip_type` as str
    ([#267](https://github.com/GoogleCloudPlatform/alloydb-python-connector/issues/267))
    ([b7b1d99](https://github.com/GoogleCloudPlatform/alloydb-python-connector/commit/b7b1d99216c68cead46be9a9a271a6258969b60f))

### Dependencies

*   Update dependency cryptography to v42.0.5
    ([#253](https://github.com/GoogleCloudPlatform/alloydb-python-connector/issues/253))
    ([cbecb8c](https://github.com/GoogleCloudPlatform/alloydb-python-connector/commit/cbecb8c9adf200e0c56de1690e4687f60dcac6ca))
*   Update dependency google-auth to v2.28.2
    ([#270](https://github.com/GoogleCloudPlatform/alloydb-python-connector/issues/270))
    ([7376a08](https://github.com/GoogleCloudPlatform/alloydb-python-connector/commit/7376a08feb307723cb9a18371f8a0565fc3abbfd))
*   Update dependency pg8000 to v1.30.5
    ([#259](https://github.com/GoogleCloudPlatform/alloydb-python-connector/issues/259))
    ([db60ca3](https://github.com/GoogleCloudPlatform/alloydb-python-connector/commit/db60ca313fa54c2edca47e78c68dd6b11489f7b1))
*   Update dependency protobuf to v4.25.3
    ([#252](https://github.com/GoogleCloudPlatform/alloydb-python-connector/issues/252))
    ([7c8f6a1](https://github.com/GoogleCloudPlatform/alloydb-python-connector/commit/7c8f6a16d2c8ff7f83f8d262799923d16d7576c7))

### Documentation

*   add header image to README
    ([#268](https://github.com/GoogleCloudPlatform/alloydb-python-connector/issues/268))
    ([9a9bd6c](https://github.com/GoogleCloudPlatform/alloydb-python-connector/commit/9a9bd6cdb37d731a3607094d287201ba787697d8))
*   Use AlloyDB API for consistency with the official documentation.
    ([#264](https://github.com/GoogleCloudPlatform/alloydb-python-connector/issues/264))
    ([9782f6e](https://github.com/GoogleCloudPlatform/alloydb-python-connector/commit/9782f6e327b6b4c2d717aab8800bc0dc6fcf6125))

### Miscellaneous Chores

*   set release to 1.0.0
    ([#275](https://github.com/GoogleCloudPlatform/alloydb-python-connector/issues/275))
    ([a38c747](https://github.com/GoogleCloudPlatform/alloydb-python-connector/commit/a38c7474397eea5b8fcd618905e2db7ac3a8020a))

## [0.4.1](https://github.com/GoogleCloudPlatform/alloydb-python-connector/compare/v0.4.0...v0.4.1) (2024-02-13)

### Dependencies

*   Update dependency aiohttp to v3.9.3
    ([#240](https://github.com/GoogleCloudPlatform/alloydb-python-connector/issues/240))
    ([3a87700](https://github.com/GoogleCloudPlatform/alloydb-python-connector/commit/3a877000252006b0e10bc96fd00938daa6160725))
*   Update dependency cryptography to v42.0.2
    ([#242](https://github.com/GoogleCloudPlatform/alloydb-python-connector/issues/242))
    ([39396ee](https://github.com/GoogleCloudPlatform/alloydb-python-connector/commit/39396eee6ab5a6c8616448fc318f50fdf981db3e))

## [0.4.0](https://github.com/GoogleCloudPlatform/alloydb-python-connector/compare/v0.3.0...v0.4.0) (2024-01-29)

### Features

*   add support for a custom user agent
    ([#233](https://github.com/GoogleCloudPlatform/alloydb-python-connector/issues/233))
    ([5c030c4](https://github.com/GoogleCloudPlatform/alloydb-python-connector/commit/5c030c43315d9178c9f4efe9305ce74f95c93c41))
*   add support for public IP connections
    ([#227](https://github.com/GoogleCloudPlatform/alloydb-python-connector/issues/227))
    ([3ed3d37](https://github.com/GoogleCloudPlatform/alloydb-python-connector/commit/3ed3d3792aa2cbc3b579b5d3cdcf180f3826b1d6))

### Dependencies

*   Update dependency aiohttp to v3.9.2
    ([#236](https://github.com/GoogleCloudPlatform/alloydb-python-connector/issues/236))
    ([4e15b6b](https://github.com/GoogleCloudPlatform/alloydb-python-connector/commit/4e15b6bcde7dae47e94238ae052d11d4a672b770))
*   Update dependency cryptography to v42
    ([#228](https://github.com/GoogleCloudPlatform/alloydb-python-connector/issues/228))
    ([05cc530](https://github.com/GoogleCloudPlatform/alloydb-python-connector/commit/05cc530cde1f5849ee7c85e4ebcd320724dc5fc3))
*   Update dependency google-auth to v2.27.0
    ([#231](https://github.com/GoogleCloudPlatform/alloydb-python-connector/issues/231))
    ([78c58b2](https://github.com/GoogleCloudPlatform/alloydb-python-connector/commit/78c58b2c778e734af5a0bb1419f9e0678b68d83a))

### Documentation

*   add asyncpg sample usage
    ([#225](https://github.com/GoogleCloudPlatform/alloydb-python-connector/issues/225))
    ([af708cb](https://github.com/GoogleCloudPlatform/alloydb-python-connector/commit/af708cbcd22265ca26ffd73cc7df2f858aaf82f1))

## [0.3.0](https://github.com/GoogleCloudPlatform/alloydb-python-connector/compare/v0.2.0...v0.3.0) (2024-01-17)

### Features

*   add auto IAM authn for `asyncpg`
    ([#210](https://github.com/GoogleCloudPlatform/alloydb-python-connector/issues/210))
    ([165b059](https://github.com/GoogleCloudPlatform/alloydb-python-connector/commit/165b05904dd4ace10cfbcd733b01452082607247))
*   add Python 3.12 support
    ([#188](https://github.com/GoogleCloudPlatform/alloydb-python-connector/issues/188))
    ([0a5864f](https://github.com/GoogleCloudPlatform/alloydb-python-connector/commit/0a5864f2a0c480313527f80cdd8e4289ba3c8d0c))
*   add support for asyncpg
    ([#199](https://github.com/GoogleCloudPlatform/alloydb-python-connector/issues/199))
    ([d14617b](https://github.com/GoogleCloudPlatform/alloydb-python-connector/commit/d14617bf01383cd61846ecb39cf3be44fd20c89a))
*   add support for auto IAM authentication to `Connector`
    ([#191](https://github.com/GoogleCloudPlatform/alloydb-python-connector/issues/191))
    ([c6c16e8](https://github.com/GoogleCloudPlatform/alloydb-python-connector/commit/c6c16e8d6dedc7aa5221aefd2ffd6bdad99566a8))
*   add support for domain-scoped projects
    ([#185](https://github.com/GoogleCloudPlatform/alloydb-python-connector/issues/185))
    ([59e10f1](https://github.com/GoogleCloudPlatform/alloydb-python-connector/commit/59e10f1f5576256a97abf767ee22cef0f8e904db))
*   allow sync init of AsyncConnector
    ([#207](https://github.com/GoogleCloudPlatform/alloydb-python-connector/issues/207))
    ([7358b37](https://github.com/GoogleCloudPlatform/alloydb-python-connector/commit/7358b37adf6ca15619f489a30a3877bd4fb7b9cf))

### Bug Fixes

*   add asyncpg as optional dep
    ([#209](https://github.com/GoogleCloudPlatform/alloydb-python-connector/issues/209))
    ([1ed5aa2](https://github.com/GoogleCloudPlatform/alloydb-python-connector/commit/1ed5aa2126af00e4096252129d4704b56a3f0997))

### Dependencies

*   Update dependency google-auth to v2.26.2
    ([#200](https://github.com/GoogleCloudPlatform/alloydb-python-connector/issues/200))
    ([0a51d2f](https://github.com/GoogleCloudPlatform/alloydb-python-connector/commit/0a51d2f68f1714c15df622fea67f1ed65f297c6e))
*   Update dependency pg8000 to v1.30.4
    ([#196](https://github.com/GoogleCloudPlatform/alloydb-python-connector/issues/196))
    ([05d18d7](https://github.com/GoogleCloudPlatform/alloydb-python-connector/commit/05d18d7b97db060afe9309263156c42daf166abe))

### Documentation

*   document auto IAM authn in README
    ([#211](https://github.com/GoogleCloudPlatform/alloydb-python-connector/issues/211))
    ([c72192c](https://github.com/GoogleCloudPlatform/alloydb-python-connector/commit/c72192cb4d994ab1c0a528b899ab461e5cb3728b))

## [0.2.0](https://github.com/GoogleCloudPlatform/alloydb-python-connector/compare/v0.1.6...v0.2.0) (2023-12-12)

### Features

*   introduce compatibility with native namespace packages
    ([#165](https://github.com/GoogleCloudPlatform/alloydb-python-connector/issues/165))
    ([8b0e2ae](https://github.com/GoogleCloudPlatform/alloydb-python-connector/commit/8b0e2ae022afb754fd69e73f0d606dd192b2dfa8))
*   wrap `generate_keys()` in future
    ([#168](https://github.com/GoogleCloudPlatform/alloydb-python-connector/issues/168))
    ([964deb0](https://github.com/GoogleCloudPlatform/alloydb-python-connector/commit/964deb05d927ac5d310b44b5962c870947f44930))

### Dependencies

*   Update dependency aiohttp to v3.9.1
    ([#170](https://github.com/GoogleCloudPlatform/alloydb-python-connector/issues/170))
    ([cf44016](https://github.com/GoogleCloudPlatform/alloydb-python-connector/commit/cf44016dcfd70f02b339189f8d0442fa3198ebb0))
*   Update dependency cryptography to v41.0.7
    ([#171](https://github.com/GoogleCloudPlatform/alloydb-python-connector/issues/171))
    ([5d95ee2](https://github.com/GoogleCloudPlatform/alloydb-python-connector/commit/5d95ee2c5bf0b5c779d4c385e1b45258ff4f6d3d))
*   Update dependency google-auth to v2.25.2
    ([#175](https://github.com/GoogleCloudPlatform/alloydb-python-connector/issues/175))
    ([cea26d7](https://github.com/GoogleCloudPlatform/alloydb-python-connector/commit/cea26d7eae7464b37e4497938e46b4d50ff00df5))

## [0.1.6](https://github.com/GoogleCloudPlatform/alloydb-python-connector/compare/v0.1.5...v0.1.6) (2023-11-14)

### Bug Fixes

*   use utcnow() for refresh calculation
    ([#155](https://github.com/GoogleCloudPlatform/alloydb-python-connector/issues/155))
    ([e33366f](https://github.com/GoogleCloudPlatform/alloydb-python-connector/commit/e33366f89faa4dd526c51d91cbf3d81033b74edf))

### Dependencies

*   Update dependency cryptography to v41.0.5
    ([#144](https://github.com/GoogleCloudPlatform/alloydb-python-connector/issues/144))
    ([01c6307](https://github.com/GoogleCloudPlatform/alloydb-python-connector/commit/01c6307cc5b870275b39cfc91406df95b3ca5d47))
*   Update dependency pg8000 to v1.30.3
    ([#149](https://github.com/GoogleCloudPlatform/alloydb-python-connector/issues/149))
    ([b487f31](https://github.com/GoogleCloudPlatform/alloydb-python-connector/commit/b487f31790a42bda20b4e43a0334c2ce3e9a5994))

## [0.1.5](https://github.com/GoogleCloudPlatform/alloydb-python-connector/compare/v0.1.4...v0.1.5) (2023-10-09)

### Dependencies

*   Update actions/setup-python action to v4.7.1
    ([#128](https://github.com/GoogleCloudPlatform/alloydb-python-connector/issues/128))
    ([9143712](https://github.com/GoogleCloudPlatform/alloydb-python-connector/commit/9143712cb5150b78f00b071246446d228786bd3e))
*   Update dependencies for github
    ([#135](https://github.com/GoogleCloudPlatform/alloydb-python-connector/issues/135))
    ([25ce604](https://github.com/GoogleCloudPlatform/alloydb-python-connector/commit/25ce604ff5669d308b2198ccce001dbcdeb79d2a))
*   Update dependency aiohttp to v3.8.6
    ([#136](https://github.com/GoogleCloudPlatform/alloydb-python-connector/issues/136))
    ([a1c0c23](https://github.com/GoogleCloudPlatform/alloydb-python-connector/commit/a1c0c235cb60364d3273afceef0d7e9d103bc3a0))
*   Update dependency cryptography to v41.0.4
    ([#123](https://github.com/GoogleCloudPlatform/alloydb-python-connector/issues/123))
    ([58da93b](https://github.com/GoogleCloudPlatform/alloydb-python-connector/commit/58da93bb7c8b66cfbd47c101e77c5d3e196838e3))
*   Update dependency pg8000 to v1.30.2
    ([#118](https://github.com/GoogleCloudPlatform/alloydb-python-connector/issues/118))
    ([8a27d53](https://github.com/GoogleCloudPlatform/alloydb-python-connector/commit/8a27d53f09d61a1de67f5053e2375e17759799a9))
*   Update dependency psycopg2-binary to v2.9.8
    ([#126](https://github.com/GoogleCloudPlatform/alloydb-python-connector/issues/126))
    ([f11afa7](https://github.com/GoogleCloudPlatform/alloydb-python-connector/commit/f11afa7c18a642083710239b170ae9c5badf2c13))
*   Update dependency psycopg2-binary to v2.9.9
    ([#130](https://github.com/GoogleCloudPlatform/alloydb-python-connector/issues/130))
    ([1a40b66](https://github.com/GoogleCloudPlatform/alloydb-python-connector/commit/1a40b6604284ec4ed40ac9b2f1d7e0eab843d901))
*   Update dependency SQLAlchemy to v2.0.21
    ([#122](https://github.com/GoogleCloudPlatform/alloydb-python-connector/issues/122))
    ([feab579](https://github.com/GoogleCloudPlatform/alloydb-python-connector/commit/feab5793469617afbf8ac2a955f7249aa2a05dd5))
*   Update github/codeql-action action to v2.21.6
    ([#111](https://github.com/GoogleCloudPlatform/alloydb-python-connector/issues/111))
    ([f51baa2](https://github.com/GoogleCloudPlatform/alloydb-python-connector/commit/f51baa28fec9391a5d2bd6959e9d8b4fe151f7f0))
*   Update github/codeql-action action to v2.21.8
    ([#116](https://github.com/GoogleCloudPlatform/alloydb-python-connector/issues/116))
    ([3241078](https://github.com/GoogleCloudPlatform/alloydb-python-connector/commit/3241078bc0cf1089913d969b71bc800e230c4a20))
*   Update github/codeql-action action to v2.21.9
    ([#125](https://github.com/GoogleCloudPlatform/alloydb-python-connector/issues/125))
    ([db941f9](https://github.com/GoogleCloudPlatform/alloydb-python-connector/commit/db941f9dcb4c89f900872fa2312011b7aecb1b4a))

### Documentation

*   add direct asyncpg IAM authentication tests
    ([#121](https://github.com/GoogleCloudPlatform/alloydb-python-connector/issues/121))
    ([70701c6](https://github.com/GoogleCloudPlatform/alloydb-python-connector/commit/70701c630bfcce44d0b3455b836275b3c5dd855d))
*   add direct psycopg2 IAM authentication test
    ([#119](https://github.com/GoogleCloudPlatform/alloydb-python-connector/issues/119))
    ([b15a3ba](https://github.com/GoogleCloudPlatform/alloydb-python-connector/commit/b15a3ba720c67a752e83c7f3ada47a974fb2e95b))

## [0.1.4](https://github.com/GoogleCloudPlatform/alloydb-python-connector/compare/v0.1.3...v0.1.4) (2023-09-08)

### Bug Fixes

*   re-use existing connections on force refresh
    ([#98](https://github.com/GoogleCloudPlatform/alloydb-python-connector/issues/98))
    ([888abd4](https://github.com/GoogleCloudPlatform/alloydb-python-connector/commit/888abd49202950a54a100e41f4d22821445b8798))

## [0.1.3](https://github.com/GoogleCloudPlatform/alloydb-python-connector/compare/v0.1.2...v0.1.3) (2023-08-08)

### Dependencies

*   Update dependency aiohttp to v3.8.5
    ([#79](https://github.com/GoogleCloudPlatform/alloydb-python-connector/issues/79))
    ([a397ea7](https://github.com/GoogleCloudPlatform/alloydb-python-connector/commit/a397ea7be96bc27abc9fc2a03a208c766924e72e))
*   Update dependency cryptography to v41.0.3
    [SECURITY]([#83]\(https://github.com/GoogleCloudPlatform/alloydb-python-connector/issues/83\))
    ([5f87fe4](https://github.com/GoogleCloudPlatform/alloydb-python-connector/commit/5f87fe415a73fd4a269355dded8f62eececf8855))
*   Update dependency pg8000 to v1.30.1
    ([#85](https://github.com/GoogleCloudPlatform/alloydb-python-connector/issues/85))
    ([2a01414](https://github.com/GoogleCloudPlatform/alloydb-python-connector/commit/2a01414f221f2e208f3dae073d3700fcd8dbec74))

## [0.1.2](https://github.com/GoogleCloudPlatform/alloydb-python-connector/compare/v0.1.1...v0.1.2) (2023-07-11)

### Dependencies

*   Update dependency cryptography to v41.0.2
    ([#70](https://github.com/GoogleCloudPlatform/alloydb-python-connector/issues/70))
    ([0ec7da9](https://github.com/GoogleCloudPlatform/alloydb-python-connector/commit/0ec7da987ea1802ad394592feb8bc1f4d41b7c8f))
*   Update dependency google-auth to v2.21.0
    ([#65](https://github.com/GoogleCloudPlatform/alloydb-python-connector/issues/65))
    ([3f98082](https://github.com/GoogleCloudPlatform/alloydb-python-connector/commit/3f9808283d9983e0f7f02a814d3360582ae0656e))
*   Update dependency pg8000 to v1.29.8
    ([#62](https://github.com/GoogleCloudPlatform/alloydb-python-connector/issues/62))
    ([9ca943d](https://github.com/GoogleCloudPlatform/alloydb-python-connector/commit/9ca943d89125a4bf220a3b54358514500c474f74))

## [0.1.1](https://github.com/GoogleCloudPlatform/alloydb-python-connector/compare/v0.1.0...v0.1.1) (2023-06-08)

### Dependencies

*   Update dependency cryptography to v41.0.1
    ([#50](https://github.com/GoogleCloudPlatform/alloydb-python-connector/issues/50))
    ([409da16](https://github.com/GoogleCloudPlatform/alloydb-python-connector/commit/409da169e93c4739dd92d6364355b2fdbeea6ed1))
*   Update dependency google-auth to v2.19.1
    ([#52](https://github.com/GoogleCloudPlatform/alloydb-python-connector/issues/52))
    ([2be8fbc](https://github.com/GoogleCloudPlatform/alloydb-python-connector/commit/2be8fbc897035ce9a88719b5317a413073408e91))
*   Update dependency pg8000 to v1.29.6
    ([#48](https://github.com/GoogleCloudPlatform/alloydb-python-connector/issues/48))
    ([ef53f03](https://github.com/GoogleCloudPlatform/alloydb-python-connector/commit/ef53f0394f87e6589adbe208a519ba2c8631aab2))

## 0.1.0 (2023-05-24)

### Features

*   Initial release of google-cloud-alloydb-connector package
