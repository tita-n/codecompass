class Codecompass < Formula
  desc "AI-powered codebase intelligence layer"
  homepage "https://codecompass.dev"
  # NOTE: bump together with the release tag; SHA256 values come from the
  # "<artifact>.sha256" files attached to the GitHub Release.
  version "0.0.1"
  license "MIT"

  on_macos do
    if Hardware::CPU.arm?
      url "https://github.com/codecompass/codecompass/releases/download/v#{version}/codecompass-darwin-arm64.tar.gz"
      sha256 "ARM64_SHA256_PLACEHOLDER"
    else
      url "https://github.com/codecompass/codecompass/releases/download/v#{version}/codecompass-darwin-x64.tar.gz"
      sha256 "X64_SHA256_PLACEHOLDER"
    end
  end

  def install
    bin.install "codecompass"
  end

  test do
    system "#{bin}/codecompass", "--version"
  end
end
