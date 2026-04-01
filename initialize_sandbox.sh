#!/usr/bin/env bash
#
# Initialize the sandbox/ inner git repository for use inside Docker containers.
# This script creates an isolated git repo with a sandboxed identity so that
# agents working inside Docker cannot access or leak the host user's credentials.
#
# Run this once from the host before starting any Docker containers.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SANDBOX_DIR="${SCRIPT_DIR}/sandbox"

if [ -d "${SANDBOX_DIR}/.git" ]; then
    echo "Sandbox git repo already exists at ${SANDBOX_DIR}/.git"
    echo "To reinitialize, remove it first: rm -rf ${SANDBOX_DIR}/.git"
    exit 1
fi

# Create sandbox directory if it doesn't exist
mkdir -p "${SANDBOX_DIR}"

# Initialize a fresh git repo
git init "${SANDBOX_DIR}"

# Set sandboxed identity - agents will commit under this throwaway identity
git -C "${SANDBOX_DIR}" config user.name "Sandbox Agent"
git -C "${SANDBOX_DIR}" config user.email "sandbox@localhost"

# Disable commit signing - no access to host signing keys
git -C "${SANDBOX_DIR}" config commit.gpgsign false

# Override signing key paths with empty values to prevent leaking host paths
# (in case global gitconfig somehow becomes visible)
git -C "${SANDBOX_DIR}" config user.signingkey ""
git -C "${SANDBOX_DIR}" config gpg.ssh.allowedSignersFile ""

# Prevent agents from adding remotes or pushing
# There is no built-in git config to block this, but having no credentials
# and no SSH keys inside the container means push will fail anyway.

echo ""
echo "Sandbox git repo initialized at: ${SANDBOX_DIR}"
echo ""
echo "Local git config for sandbox:"
git -C "${SANDBOX_DIR}" config --local --list
echo ""
echo "Mount ${SANDBOX_DIR} into your Docker container as the agent workspace."