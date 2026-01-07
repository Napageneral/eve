/**
 * Database Client - Readonly SQLite access for Eve
 * 
 * Provides readonly access to ChatStats database to prevent locking issues
 * with the backend's write operations.
 * 
 * Uses Bun's native SQLite for better performance and compatibility.
 */

import { Database } from 'bun:sqlite';
import * as path from 'path';
import * as os from 'os';

export class DatabaseClient {
  private db: Database;
  private isConnected: boolean = false;

  constructor(dbPath?: string) {
    // Default to standard ChatStats DB path if not provided
    const finalPath = dbPath || this.getDefaultDbPath();
    
    // Open database - WAL mode allows concurrent readers even without readonly flag
    // Note: Removed readonly mode as it causes "unable to open" errors in Bun SQLite
    // when the database is being actively written to by the backend
    this.db = new Database(finalPath);
    this.isConnected = true;
    
    console.log(`[DatabaseClient] Connected to: ${finalPath}`);
  }

  /**
   * Get default ChatStats database path
   */
  private getDefaultDbPath(): string {
    const appDataDir =
      process.env.EVE_APP_DIR ||
      process.env.CHATSTATS_APP_DIR ||
      path.join(os.homedir(), 'Library', 'Application Support', 'Eve');
    return path.join(appDataDir, 'eve.db');
  }

  /**
   * Execute a SELECT query and return all rows
   * 
   * Supports both positional (?) and named (:param) parameters.
   * Named parameters are converted to Bun SQLite's $param format.
   */
  queryAll<T = any>(sql: string, params: any = {}): T[] {
    if (!this.isConnected) {
      throw new Error('Database client not connected');
    }

    // Convert :param to $param for Bun SQLite compatibility
    const [convertedSql, convertedParams] = this.convertParams(sql, params);
    const stmt = this.db.prepare(convertedSql);
    return stmt.all(convertedParams) as T[];
  }

  /**
   * Execute a SELECT query and return a single row
   * 
   * Supports both positional (?) and named (:param) parameters.
   * Named parameters are converted to Bun SQLite's $param format.
   */
  queryOne<T = any>(sql: string, params: any = {}): T | null {
    if (!this.isConnected) {
      throw new Error('Database client not connected');
    }

    // Convert :param to $param for Bun SQLite compatibility
    const [convertedSql, convertedParams] = this.convertParams(sql, params);
    const stmt = this.db.prepare(convertedSql);
    const result = stmt.get(convertedParams);
    return result ? (result as T) : null;
  }

  /**
   * Execute a query and return the first column of the first row
   * 
   * Supports both positional (?) and named (:param) parameters.
   */
  queryValue<T = any>(sql: string, params: any = {}): T | null {
    const row = this.queryOne<any>(sql, params);
    if (!row) return null;
    
    const firstKey = Object.keys(row)[0];
    return row[firstKey] as T;
  }

  /**
   * Convert :param syntax to $param for Bun SQLite compatibility
   * 
   * Bun SQLite uses $param for named parameters, not :param.
   * This helper converts queries to the correct format.
   */
  private convertParams(sql: string, params: any): [string, any] {
    // If params is an array, assume positional parameters - no conversion needed
    if (Array.isArray(params)) {
      return [sql, params];
    }

    // If params is empty object, no conversion needed
    if (!params || Object.keys(params).length === 0) {
      return [sql, params];
    }

    // Convert :param to $param in SQL AND convert param keys
    const convertedSql = sql.replace(new RegExp(':([a-zA-Z_][a-zA-Z0-9_]*)', 'g'), (match, paramName) => {
      return '$' + paramName;
    });
    
    // Convert param object keys from 'key' to '$key'
    const convertedParams: any = {};
    for (const [key, value] of Object.entries(params)) {
      convertedParams['$' + key] = value;
    }
    
    return [convertedSql, convertedParams];
  }

  /**
   * Check if connected
   */
  get connected(): boolean {
    return this.isConnected;
  }

  /**
   * Close the database connection
   */
  close(): void {
    if (this.isConnected) {
      this.db.close();
      this.isConnected = false;
    }
  }
}

// Singleton instance
let dbInstance: DatabaseClient | null = null;

/**
 * Get or create database client instance
 */
export function getDbClient(dbPath?: string): DatabaseClient {
  if (!dbInstance) {
    dbInstance = new DatabaseClient(dbPath);
  }
  return dbInstance;
}

/**
 * Reset database client (useful for testing)
 */
export function resetDbClient(): void {
  if (dbInstance) {
    dbInstance.close();
    dbInstance = null;
  }
}

