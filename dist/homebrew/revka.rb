class Revka < Formula
  desc "Zero overhead. Zero compromise. 100% Rust. The fastest, smallest AI assistant."
  homepage "https://github.com/KumihoIO/Revka"
  version "@@VERSION@@"
  license any_of: ["MIT", "Apache-2.0"]

  on_macos do
    on_arm do
      url "https://github.com/KumihoIO/Revka/releases/download/v@@VERSION@@/revka-aarch64-apple-darwin.tar.gz"
      sha256 "@@DARWIN_ARM64_SHA@@"
    end
  end

  on_linux do
    on_arm do
      url "https://github.com/KumihoIO/Revka/releases/download/v@@VERSION@@/revka-aarch64-unknown-linux-gnu.tar.gz"
      sha256 "@@LINUX_ARM64_SHA@@"
    end
    on_intel do
      url "https://github.com/KumihoIO/Revka/releases/download/v@@VERSION@@/revka-x86_64-unknown-linux-gnu.tar.gz"
      sha256 "@@LINUX_X86_64_SHA@@"
    end
  end

  def install
    bin.install "revka"
  end

  test do
    assert_match "revka", shell_output("#{bin}/revka --version")
  end
end
