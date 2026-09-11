PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT OR IGNORE INTO schema_version(version) VALUES (3);
INSERT OR IGNORE INTO schema_version(version) VALUES (4);
INSERT OR IGNORE INTO schema_version(version) VALUES (5);

CREATE TABLE IF NOT EXISTS households (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    household_id INTEGER NOT NULL REFERENCES households(id),
    name TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('owner', 'user')),
    channel_identity TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS user_channel_identities (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    channel TEXT NOT NULL,
    identity_kind TEXT NOT NULL CHECK(identity_kind IN ('e164', 'jid', 'lid', 'requester_id')),
    identity_value TEXT NOT NULL,
    verified_by INTEGER REFERENCES users(id),
    verified_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(channel, identity_value),
    UNIQUE(user_id, channel, identity_kind, identity_value)
);

CREATE TABLE IF NOT EXISTS media_objects (
    storage_id TEXT PRIMARY KEY,
    sha256 TEXT NOT NULL UNIQUE,
    storage_ref TEXT NOT NULL UNIQUE,
    mime_type TEXT NOT NULL,
    byte_size INTEGER NOT NULL CHECK(byte_size >= 0),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS supermarkets (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    chain TEXT NOT NULL,
    store_name TEXT,
    UNIQUE(chain, store_name)
);

CREATE TABLE IF NOT EXISTS receipts (
    id INTEGER PRIMARY KEY,
    household_id INTEGER NOT NULL REFERENCES households(id),
    supermarket_id INTEGER NOT NULL REFERENCES supermarkets(id),
    content_hash TEXT NOT NULL UNIQUE,
    purchase_date TEXT,
    purchase_date_source TEXT NOT NULL CHECK(purchase_date_source IN ('ocr', 'inferred', 'user_confirmed', 'unknown')),
    purchase_date_needs_confirmation INTEGER NOT NULL CHECK(purchase_date_needs_confirmation IN (0, 1)),
    article_count INTEGER,
    printed_product_line_count INTEGER,
    subtotal_gross_cents INTEGER NOT NULL,
    immediate_discounts_cents INTEGER NOT NULL DEFAULT 0,
    coupon_applied_cents INTEGER NOT NULL DEFAULT 0,
    amount_paid_cents INTEGER NOT NULL,
    reward_generated_cents INTEGER NOT NULL DEFAULT 0,
    currency TEXT NOT NULL DEFAULT 'EUR',
    raw_ocr_text TEXT NOT NULL,
    initial_extraction_json TEXT NOT NULL,
    normalized_result_json TEXT NOT NULL,
    extraction_confidence TEXT,
    validation_status TEXT NOT NULL CHECK(validation_status IN ('valid', 'needs_review')),
    created_by INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    deleted_at TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS receipt_images (
    id INTEGER PRIMARY KEY,
    receipt_id INTEGER NOT NULL REFERENCES receipts(id) ON DELETE RESTRICT,
    ordinal INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    storage_id TEXT NOT NULL,
    storage_ref TEXT NOT NULL,
    mime_type TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(receipt_id, ordinal),
    UNIQUE(receipt_id, sha256)
);

CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY,
    household_id INTEGER NOT NULL REFERENCES households(id),
    stable_id TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(household_id, name)
);

CREATE TABLE IF NOT EXISTS product_families (
    id INTEGER PRIMARY KEY,
    household_id INTEGER NOT NULL REFERENCES households(id),
    stable_id TEXT NOT NULL UNIQUE,
    category_id INTEGER NOT NULL REFERENCES categories(id),
    name TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(household_id, category_id, name)
);

CREATE TABLE IF NOT EXISTS comparable_products (
    id INTEGER PRIMARY KEY,
    household_id INTEGER NOT NULL REFERENCES households(id),
    stable_id TEXT NOT NULL UNIQUE,
    product_family_id INTEGER NOT NULL REFERENCES product_families(id),
    name TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(household_id, product_family_id, name)
);

CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY,
    household_id INTEGER NOT NULL REFERENCES households(id),
    stable_id TEXT NOT NULL UNIQUE,
    comparable_product_id INTEGER NOT NULL REFERENCES comparable_products(id),
    name TEXT NOT NULL,
    base_unit TEXT CHECK(base_unit IN ('unit', 'kg', 'L', 'egg', 'other')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(household_id, stable_id)
);

CREATE TABLE IF NOT EXISTS product_aliases (
    id INTEGER PRIMARY KEY,
    household_id INTEGER NOT NULL REFERENCES households(id),
    supermarket_id INTEGER REFERENCES supermarkets(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    alias_text TEXT NOT NULL,
    product_code TEXT,
    confidence TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(household_id, supermarket_id, alias_text)
);

CREATE TABLE IF NOT EXISTS receipt_items (
    id INTEGER PRIMARY KEY,
    receipt_id INTEGER NOT NULL REFERENCES receipts(id) ON DELETE RESTRICT,
    original_text TEXT NOT NULL,
    product_code TEXT,
    product_id INTEGER REFERENCES products(id),
    purchase_quantity TEXT NOT NULL,
    purchase_unit TEXT NOT NULL,
    pack_count INTEGER,
    content_per_unit_value TEXT,
    content_per_unit_unit TEXT,
    normalized_quantity TEXT,
    normalized_unit TEXT,
    unit_price_cents INTEGER,
    line_gross_cents INTEGER NOT NULL,
    line_discount_cents INTEGER NOT NULL DEFAULT 0,
    line_final_cents INTEGER NOT NULL,
    extraction_confidence TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS receipt_adjustments (
    id INTEGER PRIMARY KEY,
    receipt_id INTEGER NOT NULL REFERENCES receipts(id) ON DELETE RESTRICT,
    receipt_item_id INTEGER REFERENCES receipt_items(id),
    type TEXT NOT NULL,
    description TEXT NOT NULL,
    original_text TEXT NOT NULL,
    code TEXT,
    amount_cents INTEGER NOT NULL,
    affects_amount_paid INTEGER NOT NULL CHECK(affects_amount_paid IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS receipt_validations (
    id INTEGER PRIMARY KEY,
    receipt_id INTEGER NOT NULL REFERENCES receipts(id) ON DELETE RESTRICT,
    name TEXT NOT NULL,
    passed INTEGER NOT NULL CHECK(passed IN (0, 1)),
    expected TEXT,
    actual TEXT,
    tolerance TEXT,
    message TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS receipt_corrections (
    id INTEGER PRIMARY KEY,
    receipt_id INTEGER NOT NULL REFERENCES receipts(id) ON DELETE RESTRICT,
    corrected_by INTEGER REFERENCES users(id),
    field_path TEXT NOT NULL,
    old_value_json TEXT NOT NULL,
    new_value_json TEXT NOT NULL,
    reason TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS receipt_revisions (
    id INTEGER PRIMARY KEY,
    receipt_id INTEGER NOT NULL REFERENCES receipts(id) ON DELETE RESTRICT,
    revision_no INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    changed_by INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(receipt_id, revision_no)
);

CREATE TABLE IF NOT EXISTS receipt_batches (
    id TEXT PRIMARY KEY,
    household_id INTEGER NOT NULL REFERENCES households(id),
    group_jid TEXT NOT NULL,
    sender_identity TEXT NOT NULL,
    created_by INTEGER NOT NULL REFERENCES users(id),
    status TEXT NOT NULL CHECK(status IN ('open', 'finalized', 'failed')),
    closure_mode TEXT CHECK(closure_mode IN ('short', 'long', 'explicit')),
    opened_at TEXT NOT NULL,
    last_image_at TEXT NOT NULL,
    close_after_at TEXT NOT NULL,
    received_at TEXT NOT NULL,
    receipt_id INTEGER REFERENCES receipts(id),
    error_code TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS receipt_batch_images (
    id INTEGER PRIMARY KEY,
    batch_id TEXT NOT NULL REFERENCES receipt_batches(id) ON DELETE RESTRICT,
    ordinal INTEGER NOT NULL,
    storage_id TEXT NOT NULL REFERENCES media_objects(storage_id) ON DELETE RESTRICT,
    ocr_text TEXT NOT NULL,
    ocr_confidence TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(batch_id, ordinal),
    UNIQUE(batch_id, storage_id)
);

CREATE TABLE IF NOT EXISTS receipt_reviews (
    id INTEGER PRIMARY KEY,
    receipt_id INTEGER NOT NULL REFERENCES receipts(id) ON DELETE RESTRICT,
    batch_id TEXT REFERENCES receipt_batches(id) ON DELETE RESTRICT,
    field_path TEXT NOT NULL,
    question TEXT NOT NULL,
    proposed_value_json TEXT,
    status TEXT NOT NULL CHECK(status IN ('open', 'resolved', 'cancelled')) DEFAULT 'open',
    resolved_by INTEGER REFERENCES users(id),
    answer_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    resolved_at TEXT
);

CREATE TABLE IF NOT EXISTS receipt_batch_results (
    batch_id TEXT PRIMARY KEY REFERENCES receipt_batches(id) ON DELETE RESTRICT,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    delivered_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_receipts_purchase_date ON receipts(purchase_date);
CREATE INDEX IF NOT EXISTS idx_receipts_supermarket_date ON receipts(supermarket_id, purchase_date);
CREATE INDEX IF NOT EXISTS idx_receipt_items_product ON receipt_items(product_id);
CREATE INDEX IF NOT EXISTS idx_comparable_family ON comparable_products(product_family_id);
CREATE INDEX IF NOT EXISTS idx_product_comparable ON products(comparable_product_id);
CREATE INDEX IF NOT EXISTS idx_product_aliases_lookup ON product_aliases(supermarket_id, alias_text);
CREATE INDEX IF NOT EXISTS idx_adjustments_receipt_code ON receipt_adjustments(receipt_id, code);
CREATE INDEX IF NOT EXISTS idx_revisions_receipt ON receipt_revisions(receipt_id, revision_no);
CREATE INDEX IF NOT EXISTS idx_channel_identities_lookup ON user_channel_identities(channel, identity_value);
CREATE INDEX IF NOT EXISTS idx_batches_context ON receipt_batches(group_jid, sender_identity, status);
CREATE UNIQUE INDEX IF NOT EXISTS idx_one_open_batch_per_sender
ON receipt_batches(group_jid, sender_identity) WHERE status = 'open';
CREATE INDEX IF NOT EXISTS idx_batches_due ON receipt_batches(status, close_after_at);
CREATE INDEX IF NOT EXISTS idx_batch_images_batch ON receipt_batch_images(batch_id, ordinal);
CREATE INDEX IF NOT EXISTS idx_reviews_context ON receipt_reviews(status, batch_id, receipt_id);
CREATE INDEX IF NOT EXISTS idx_batch_results_delivery ON receipt_batch_results(delivered_at, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_one_open_review_per_field
ON receipt_reviews(receipt_id, field_path) WHERE status = 'open';
