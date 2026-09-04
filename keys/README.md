# Signing keys

`dev_ed25519.key` is a throwaway development key. It is committed on purpose
so that a clean clone builds signed images and the test suite runs without
any secret material. It protects nothing: anyone with this repository can
sign an image the lab bootloader accepts.

Files:

| File | Content |
|------|---------|
| `dev_ed25519.key` | 32 byte Ed25519 private seed, hex |
| `dev_ed25519.pub` | 32 byte public key, hex |
| `../firmware/common/public_key.c` | the same public key as a C array, compiled into the bootloader and the application |

Do not rotate or delete the development key: the signed images in
`build/`, the fleet and fault tests, and the committed example artefacts all
depend on it.

For anything real: run `python tools/keygen.py --dir <secure dir> --name
<product>` on a machine that keeps the private half off the repository,
commit only the regenerated `firmware/common/public_key.c`, and rebuild the
bootloader. The bootloader accepts images signed by exactly one key, and the
application cannot change it: the key lives in the bootloader's own flash
sectors, which no image ever writes.
