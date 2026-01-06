/**
 * Contact SQL Queries
 * 
 * Ported from backend/repositories/contacts.py
 */

import { DatabaseClient } from '../client.js';
import { Contact, ContactIdentifier } from '../types.js';

/**
 * Get contact by ID
 */
export function getContactById(
  db: DatabaseClient,
  contactId: number
): Contact | null {
  const sql = `SELECT * FROM contacts WHERE id = :contactId LIMIT 1`;
  return db.queryOne<Contact>(sql, { contactId });
}

/**
 * Get "me" contact (is_me = 1)
 */
export function getMeContact(
  db: DatabaseClient
): Contact | null {
  const sql = `SELECT * FROM contacts WHERE is_me = 1 LIMIT 1`;
  return db.queryOne<Contact>(sql);
}

/**
 * Get contacts by IDs
 */
export function getContactsByIds(
  db: DatabaseClient,
  contactIds: number[]
): Contact[] {
  if (contactIds.length === 0) return [];

  const sql = `
    SELECT * 
    FROM contacts
    WHERE id IN (${contactIds.map((_, i) => `:contactId${i}`).join(', ')})
  `;

  const params: any = {};
  contactIds.forEach((id, i) => {
    params[`contactId${i}`] = id;
  });

  return db.queryAll<Contact>(sql, params);
}

/**
 * Get contact identifiers for a contact
 */
export function getIdentifiersForContact(
  db: DatabaseClient,
  contactId: number
): ContactIdentifier[] {
  const sql = `
    SELECT * 
    FROM contact_identifiers
    WHERE contact_id = :contactId
    ORDER BY is_primary DESC, last_used DESC
  `;
  return db.queryAll<ContactIdentifier>(sql, { contactId });
}

/**
 * Get contacts for chat (via chat_participants)
 */
export function getContactsForChat(
  db: DatabaseClient,
  chatId: number
): Contact[] {
  const sql = `
    SELECT c.*
    FROM contacts c
    JOIN chat_participants cp ON cp.contact_id = c.id
    WHERE cp.chat_id = :chatId
    ORDER BY c.name
  `;
  return db.queryAll<Contact>(sql, { chatId });
}

/**
 * Create contact name map (id -> name)
 */
export function getContactNameMap(
  db: DatabaseClient,
  contactIds: number[]
): Map<number, string> {
  const contacts = getContactsByIds(db, contactIds);
  const map = new Map<number, string>();
  
  for (const contact of contacts) {
    const name = contact.name || contact.nickname || `Contact ${contact.id}`;
    map.set(contact.id, name);
  }
  
  return map;
}

