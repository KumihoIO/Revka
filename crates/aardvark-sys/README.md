# aardvark-sys

Low-level bindings for the [Total Phase Aardvark](https://www.totalphase.com/products/aardvark-i2cspi/) I2C/SPI/GPIO USB adapter.

## Installing the Total Phase SDK

The Aardvark SDK (`aardvark.h`, `aardvark.so` / `.dll` / `.dylib`) is **not checked into this repository** because it is distributed by Total Phase under terms that are not compatible with Revka's dual MIT/Apache-2.0 license.

Download the SDK from <https://www.totalphase.com/products/aardvark-software-api/> and make the shared library available to Revka in one of the following ways:

1. **Environment variable (preferred)**

   ```bash
   export REVKA_AARDVARK_LIB=/absolute/path/to/aardvark.so
   ```

2. **Next to the Revka binary**

   Copy the shared library (`aardvark.so` on Linux/macOS, `aardvark.dll` on Windows) into the same directory as the `revka` executable.

3. **Dev builds: crate `vendor/` directory**

   Drop the file into `crates/aardvark-sys/vendor/`. The `.gitignore` in that directory prevents accidental commits.

## Behavior without the SDK

If no copy of the library is found, every `AardvarkHandle` method that needs it returns `AardvarkError::LibraryNotFound`. Hardware discovery simply reports zero Aardvark adapters; the rest of Revka continues to function.

## Architecture notes

This crate is the only workspace member that permits `unsafe` code. The rest of the workspace retains `#![forbid(unsafe_code)]`. The FFI surface is kept as narrow as possible — see `src/lib.rs` for the full list of symbols loaded from the shared library.
