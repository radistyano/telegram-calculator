# USDT Calculator Telegram Bot

A Telegram bot for calculating USDT buy/sell transactions with customizable rates and fees.

## Features

- **Automatic Price Calculator**: Calculate USDT buy/sell prices with customizable rates and fees
- **Admin Panel**: Manage rates, fees, and view profit statistics
- **User Panel**: Simple interface for users to calculate USDT transactions

## Setup

1. Clone this repository
2. Install dependencies:
   ```
   pip install -r requirements.txt
   ```
3. Create a `.env` file with the following variables:
   ```
   TELEGRAM_BOT_TOKEN=your_bot_token_here
   ADMIN_USER_IDS=123456789,987654321
   ```
4. Run the bot:
   ```
   python main.py
   ```

## Usage

### User Commands
- `/start` - Start the bot and show main menu
- `/help` - Show help information

### Admin Commands
- `/admin` - Access admin panel (restricted to admin users)

## Database Structure

The bot uses SQLite to store:
- Current buy/sell rates
- Fee ranges and amounts
- Transaction statistics 