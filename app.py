import os
from flask import Flask, jsonify, render_template, request
import sqlite3
from datetime import date, datetime

app = Flask(__name__)

# --- Database Setup ---
DATA_DIR = "data"
DB_FILE = os.path.join(DATA_DIR, "wallet.db")

def get_db_connection():
    """Establishes a connection to the SQLite database."""
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')  # Enable foreign key constraints
    return conn

def init_db():
    """Initializes the database and applies any necessary schema migrations."""
    # Ensure the data directory exists.
    os.makedirs(DATA_DIR, exist_ok=True)

    # Check if database exists for migration purposes
    db_exists = os.path.exists(DB_FILE)

    conn = get_db_connection()
    cursor = conn.cursor()

    if not db_exists:
        print("Initializing database...")

        # Banknotes table
        cursor.execute('''
            CREATE TABLE banknotes (
                id INTEGER PRIMARY KEY,
                value INTEGER UNIQUE NOT NULL,
                name TEXT NOT NULL,
                count INTEGER NOT NULL DEFAULT 0
            )
        ''')

        # Transactions table
        cursor.execute('''
            CREATE TABLE transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                note TEXT,
                amount INTEGER NOT NULL,
                type TEXT NOT NULL DEFAULT 'expense', -- 'expense' or 'income'
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Transaction details table to log banknote movements
        cursor.execute('''
            CREATE TABLE transaction_details (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                transaction_id INTEGER NOT NULL,
                banknote_value INTEGER NOT NULL,
                count_change INTEGER NOT NULL, -- positive for received, negative for paid
                FOREIGN KEY (transaction_id) REFERENCES transactions (id) ON DELETE CASCADE
            )
        ''')

        # Create indexes for performance
        cursor.execute('CREATE INDEX idx_transactions_timestamp ON transactions(timestamp)')
        cursor.execute('CREATE INDEX idx_transactions_type ON transactions(type)')
        cursor.execute('CREATE INDEX idx_transaction_details_transaction_id ON transaction_details(transaction_id)')

        # Pre-populate with Indonesian Rupiah denominations
        banknotes = [
            (100000, 'Rp 100,000'), (50000, 'Rp 50,000'), (20000, 'Rp 20,000'),
            (10000, 'Rp 10,000'), (5000, 'Rp 5,000'), (2000, 'Rp 2,000'), (1000, 'Rp 1,000')
        ]
        cursor.executemany('INSERT INTO banknotes (value, name) VALUES (?, ?)', banknotes)

        conn.commit()
        print("Database initialized.")
    else:
        # Handle migrations for existing databases
        try:
            # Check if indexes exist, create if missing
            cursor.execute("SELECT name FROM sqlite_master WHERE type='index' AND name='idx_transactions_timestamp'")
            if not cursor.fetchone():
                print("Adding missing indexes...")
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_transactions_timestamp ON transactions(timestamp)')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_transactions_type ON transactions(type)')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_transaction_details_transaction_id ON transaction_details(transaction_id)')
                conn.commit()
                print("Indexes added.")
        except sqlite3.Error as e:
            print(f"Migration warning: {e}")

    conn.close()

# --- API Endpoints ---

@app.route('/')
def index():
    """Renders the main HTML page."""
    return render_template('index.html')

@app.route('/api/wallet', methods=['GET'])
def get_wallet_status():
    """Retrieves the current count of each banknote and the total cash."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT value, name, count FROM banknotes ORDER BY value DESC')
    banknotes = [dict(row) for row in cursor.fetchall()]
    
    total_cash = sum(note['value'] * note['count'] for note in banknotes)
    
    conn.close()
    return jsonify({
        'banknotes': banknotes,
        'total_cash': total_cash
    })

@app.route('/api/transaction', methods=['POST'])
def create_transaction():
    """Handles a new transaction (expense or income), updating banknote counts."""
    data = request.json
    if not data:
        return jsonify({'error': 'Request body must contain JSON data.'}), 400

    note = data.get('note', 'Transaction')
    amount = data.get('amount', 0)
    transaction_type = data.get('type', 'expense')
    paid_with = data.get('paid_with', {})
    change_received = data.get('change_received', {})
    timestamp_str = data.get('timestamp')

    # Validate transaction type
    if transaction_type not in ['expense', 'income']:
        return jsonify({'error': 'Invalid transaction type. Must be "expense" or "income".'}), 400

    # Validate amount
    if not isinstance(amount, (int, float)) or amount <= 0:
        return jsonify({'error': 'Amount must be a positive number.'}), 400

    # Validate paid_with and change_received are dictionaries
    if not isinstance(paid_with, dict):
        return jsonify({'error': 'paid_with must be a dictionary.'}), 400
    if not isinstance(change_received, dict):
        return jsonify({'error': 'change_received must be a dictionary.'}), 400

    # Validate timestamp format
    if timestamp_str:
        try:
            datetime.strptime(timestamp_str, '%Y-%m-%d %H:%M:%S')
        except ValueError:
            return jsonify({'error': 'Invalid timestamp format. Expected YYYY-MM-DD HH:MM:SS.'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        details_to_log = []
        if transaction_type == 'expense':
            # Validate that expense has payment method
            if not paid_with:
                raise ValueError("Expense transaction must specify banknotes paid with.")

            # 1. Deduct notes paid with
            for value_str, count in paid_with.items():
                if not isinstance(count, int) or count < 0:
                    raise ValueError(f"Invalid count for banknote {value_str}.")
                if count > 0:
                    cursor.execute('UPDATE banknotes SET count = count - ? WHERE value = ? AND count >= ?', (count, int(value_str), count))
                    if cursor.rowcount == 0:
                        cursor.execute('SELECT count FROM banknotes WHERE value = ?', (int(value_str),))
                        row = cursor.fetchone()
                        current_count = row['count'] if row else 0
                        raise ValueError(f"Insufficient Rp {value_str} notes in wallet. Available: {current_count}, Required: {count}.")
                    details_to_log.append((0, int(value_str), -count)) # Temp tx_id
            # 2. Add notes received as change
            for value_str, count in change_received.items():
                if not isinstance(count, int) or count < 0:
                    raise ValueError(f"Invalid count for change received {value_str}.")
                if count > 0:
                    cursor.execute('UPDATE banknotes SET count = count + ? WHERE value = ?', (count, int(value_str)))
                    details_to_log.append((0, int(value_str), count))

        elif transaction_type == 'income':
            # For income, 'change_received' is interpreted as 'notes_received'
            if not change_received:
                raise ValueError("Income transaction must specify banknotes received.")
            for value_str, count in change_received.items():
                if not isinstance(count, int) or count < 0:
                    raise ValueError(f"Invalid count for income {value_str}.")
                if count > 0:
                    cursor.execute('UPDATE banknotes SET count = count + ? WHERE value = ?', (count, int(value_str)))
                    details_to_log.append((0, int(value_str), count))

        # 3. Log the transaction
        cursor.execute(
            'INSERT INTO transactions (note, amount, type, timestamp) VALUES (?, ?, ?, ?)',
            (note, amount, transaction_type, timestamp_str)
        )
        transaction_id = cursor.lastrowid

        # 4. Update and log transaction details
        final_details = [(transaction_id, val, cnt) for _, val, cnt in details_to_log]
        cursor.executemany('INSERT INTO transaction_details (transaction_id, banknote_value, count_change) VALUES (?, ?, ?)', final_details)

        conn.commit()
    except (ValueError, sqlite3.Error) as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 400
    finally:
        conn.close()
        
    return jsonify({'message': 'Transaction successful'}), 201

@app.route('/api/wallet/adjust', methods=['POST'])
def adjust_wallet():
    """Directly updates the counts of each banknote in the wallet."""
    data = request.json
    if not data:
        return jsonify({'error': 'Request body must contain JSON data.'}), 400

    adjustments = data.get('adjustments', {})
    if not isinstance(adjustments, dict):
        return jsonify({'error': 'Adjustments must be a dictionary.'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        for value_str, count in adjustments.items():
            # Validate count is non-negative
            count_int = int(count)
            if count_int < 0:
                raise ValueError(f"Count for Rp {value_str} cannot be negative.")

            cursor.execute(
                'UPDATE banknotes SET count = ? WHERE value = ?',
                (count_int, int(value_str))
            )
            if cursor.rowcount == 0:
                raise ValueError(f"Banknote value Rp {value_str} not found.")

        conn.commit()
    except (ValueError, sqlite3.Error) as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 400
    finally:
        conn.close()

    return jsonify({'message': 'Wallet adjusted successfully'}), 200

@app.route('/api/history', methods=['GET'])
def get_history():
    """Retrieves transactions with optional filtering and sorting."""
    filter_period = request.args.get('filter_period', 'all')
    sort_by = request.args.get('sort_by', 'date_desc')

    conn = get_db_connection()
    cursor = conn.cursor()

    query = 'SELECT id, note, amount, type, timestamp FROM transactions'
    params = []

    # Filtering
    if filter_period == 'today':
        query += ' WHERE DATE(timestamp) = DATE(?)'
        params.append(date.today().isoformat())
    elif filter_period == 'this_month':
        query += ' WHERE STRFTIME("%Y-%m", timestamp) = STRFTIME("%Y-%m", ?)'
        params.append(date.today().isoformat())
    
    # Sorting
    if sort_by == 'date_asc':
        query += ' ORDER BY timestamp ASC'
    elif sort_by == 'amount_desc':
        query += ' ORDER BY amount DESC, timestamp DESC'
    elif sort_by == 'amount_asc':
        query += ' ORDER BY amount ASC, timestamp DESC'
    else: # Default 'date_desc'
        query += ' ORDER BY timestamp DESC'

    # Removed LIMIT 20 to show all filtered/sorted results
    # If pagination is desired later, it can be added here.

    cursor.execute(query, params)
    history = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return jsonify(history)


@app.route('/api/transaction/<int:transaction_id>', methods=['GET'])
def get_transaction(transaction_id):
    """Retrieves a single transaction with its details."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('SELECT id, note, amount, type, timestamp FROM transactions WHERE id = ?', (transaction_id,))
    transaction = cursor.fetchone()
    
    if not transaction:
        conn.close()
        return jsonify({'error': 'Transaction not found.'}), 404
        
    transaction_data = dict(transaction)
    
    cursor.execute('SELECT banknote_value, count_change FROM transaction_details WHERE transaction_id = ?', (transaction_id,))
    details = cursor.fetchall()
    conn.close()
    
    # For income, all details are positive counts
    if transaction_data['type'] == 'income':
        transaction_data['notes_received'] = {d['banknote_value']: d['count_change'] for d in details}
    else: # For expense
        transaction_data['paid_with'] = {d['banknote_value']: -d['count_change'] for d in details if d['count_change'] < 0}
        transaction_data['change_received'] = {d['banknote_value']: d['count_change'] for d in details if d['count_change'] > 0}

    return jsonify(transaction_data)


@app.route('/api/transaction/<int:transaction_id>', methods=['PUT'])
def update_transaction(transaction_id):
    """Updates a specific transaction, including note, timestamp, amount, and banknote movements."""
    data = request.json or {}
    new_note = data.get('note')
    new_timestamp_str = data.get('timestamp')
    new_amount = data.get('amount')
    new_paid_with = data.get('paid_with')
    new_change_received = data.get('change_received')

    is_wallet_update = new_paid_with is not None or new_change_received is not None

    if not any([new_note is not None, new_timestamp_str is not None, new_amount is not None, is_wallet_update]):
        return jsonify({'error': 'At least one field must be provided for update.'}), 400

    if new_timestamp_str:
        try:
            datetime.strptime(new_timestamp_str, '%Y-%m-%d %H:%M:%S')
        except ValueError:
            return jsonify({'error': 'Invalid timestamp format. Expected YYYY-MM-DD HH:MM:SS.'}), 400
    
    if is_wallet_update and new_amount is None:
        return jsonify({'error': 'Amount is required when updating banknote details.'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # --- Get original transaction data ---
        cursor.execute('SELECT note, amount, type, timestamp FROM transactions WHERE id = ?', (transaction_id,))
        transaction = cursor.fetchone()
        if not transaction:
            return jsonify({'error': 'Transaction not found.'}), 404
        
        # --- If banknote details are being updated, revert old and apply new ---
        if is_wallet_update:
            # 1. Get and revert old banknote movements
            cursor.execute('SELECT banknote_value, count_change FROM transaction_details WHERE transaction_id = ?', (transaction_id,))
            old_details = cursor.fetchall()
            for detail in old_details:
                cursor.execute('UPDATE banknotes SET count = count - ? WHERE value = ?', (detail['count_change'], detail['banknote_value']))
                cursor.execute('SELECT count FROM banknotes WHERE value = ?', (detail['banknote_value'],))
                if cursor.fetchone()['count'] < 0:
                     raise ValueError(f"Reverting transaction would result in negative count for Rp {detail['banknote_value']}.")

            # 2. Apply new banknote movements
            details_to_log = []
            if transaction['type'] == 'expense':
                paid_with = new_paid_with or {}
                change_received = new_change_received or {}
                for value_str, count in paid_with.items():
                    if count > 0:
                        cursor.execute('UPDATE banknotes SET count = count - ? WHERE value = ? AND count >= ?', (count, int(value_str), count))
                        if cursor.rowcount == 0:
                            raise ValueError(f"Not enough {value_str} notes in wallet for new payment.")
                        details_to_log.append((transaction_id, int(value_str), -count))
                for value_str, count in change_received.items():
                    if count > 0:
                        cursor.execute('UPDATE banknotes SET count = count + ? WHERE value = ?', (count, int(value_str)))
                        details_to_log.append((transaction_id, int(value_str), count))
            
            elif transaction['type'] == 'income':
                notes_received = new_change_received or {}
                if not notes_received:
                     raise ValueError("Income transaction update must have notes received.")
                for value_str, count in notes_received.items():
                    if count > 0:
                        cursor.execute('UPDATE banknotes SET count = count + ? WHERE value = ?', (count, int(value_str)))
                        details_to_log.append((transaction_id, int(value_str), count))

            # 3. Update transaction_details table
            cursor.execute('DELETE FROM transaction_details WHERE transaction_id = ?', (transaction_id,))
            if details_to_log:
                cursor.executemany('INSERT INTO transaction_details (transaction_id, banknote_value, count_change) VALUES (?, ?, ?)', details_to_log)

        # --- Update the main transaction record ---
        final_note = new_note if new_note is not None else transaction['note']
        final_timestamp = new_timestamp_str if new_timestamp_str is not None else transaction['timestamp']
        final_amount = new_amount if new_amount is not None else transaction['amount']
        
        cursor.execute(
            'UPDATE transactions SET note = ?, timestamp = ?, amount = ? WHERE id = ?',
            (final_note, final_timestamp, final_amount, transaction_id)
        )
        if cursor.rowcount == 0:
            raise sqlite3.Error("Transaction not found or not updated during final update.")

        conn.commit()
    except (ValueError, sqlite3.Error) as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 400
    finally:
        conn.close()

    return jsonify({'message': 'Transaction updated successfully.'}), 200


@app.route('/api/transaction/<int:transaction_id>', methods=['DELETE'])
def delete_transaction(transaction_id):
    """Deletes a transaction and reverts the banknote counts."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # 1. Get the banknote movements for the transaction
        cursor.execute(
            'SELECT banknote_value, count_change FROM transaction_details WHERE transaction_id = ?',
            (transaction_id,)
        )
        details = cursor.fetchall()
        if not details:
            return jsonify({'error': 'Transaction not found or has no details to revert.'}), 404

        # 2. Revert the banknote counts and check for negative values
        for detail in details:
            # The change is reversed: if we paid (-), we add back (+). If we received (+), we subtract (-).
            if detail['count_change'] > 0:
                cursor.execute(
                    'UPDATE banknotes SET count = count - ? WHERE value = ? AND count >= ?',
                    (detail['count_change'], detail['banknote_value'], detail['count_change'])
                )
                if cursor.rowcount == 0:
                    raise ValueError(f"Cannot delete transaction: reverting would result in negative count for Rp {detail['banknote_value']}.")
            else:
                cursor.execute(
                    'UPDATE banknotes SET count = count + ? WHERE value = ?',
                    (-detail['count_change'], detail['banknote_value'])
                )

        # 3. Delete the details and the transaction itself
        cursor.execute('DELETE FROM transaction_details WHERE transaction_id = ?', (transaction_id,))
        cursor.execute('DELETE FROM transactions WHERE id = ?', (transaction_id,))

        conn.commit()
    except (ValueError, sqlite3.Error) as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 400
    finally:
        conn.close()

    return jsonify({'message': 'Transaction deleted successfully'}), 200

# Initialize the database when the app starts
init_db()
