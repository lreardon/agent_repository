/**
 * Ed25519 request signing for k6.
 *
 * k6 doesn't have native Ed25519 support, so we shell out to a small
 * Python helper for signing. The helper is called once per setup()
 * to pre-generate a batch of signed headers for use during the test.
 *
 * For the load test we take a simpler approach: we register agents
 * via a Python setup script that returns pre-signed headers, or we
 * use a shared-secret HMAC bypass for load testing only.
 *
 * This module provides the header-building utilities.
 */

import crypto from 'k6/crypto';

/**
 * Build the body hash component of the signature message.
 */
export function bodyHash(body) {
  if (!body || body === '') return crypto.sha256('', 'hex');
  return crypto.sha256(body, 'hex');
}

/**
 * Build an ISO timestamp.
 */
export function isoTimestamp() {
  return new Date().toISOString();
}

/**
 * Generate a random hex nonce.
 */
export function randomNonce() {
  const bytes = new Uint8Array(16);
  for (let i = 0; i < 16; i++) {
    bytes[i] = Math.floor(Math.random() * 256);
  }
  return Array.from(bytes).map(b => b.toString(16).padStart(2, '0')).join('');
}
