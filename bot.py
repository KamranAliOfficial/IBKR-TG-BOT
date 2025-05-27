import asyncio
import logging
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters
)
import nest_asyncio
nest_asyncio.apply()
from ib_insync import IB, util

# ✅ Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ✅ Replace with your bot token and chat_id
TELEGRAM_BOT_TOKEN = "7537301802:AAHNMUItC6y8PWEICOwt2JxlmvPVJVuOkbs"
CHAT_ID = 6409841008  # 🔒 Use your actual Telegram chat ID

# ✅ User state tracking
default_state = {"messages": [], "order": {}, "step": None, "action": None}
user_data = {}

# ✅ TradingBot class
class TradingBot:
    def __init__(self):
        self.app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
        self.ib = IB()

    async def connect_ibkr(self):
        try:
            self.ib.connect("127.0.0.1", 7497, clientId=1)
            logger.info("✅ Connected to IBKR")
        except Exception as e:
            logger.error(f"❌ IBKR connection failed: {e}")
            raise

    async def disconnect_ibkr(self):
        self.ib.disconnect()
        logger.info("🔌 Disconnected from IBKR")

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        keyboard = [
            [InlineKeyboardButton("📈 Buy", callback_data="buy"),
             InlineKeyboardButton("📉 Sell", callback_data="sell")],
            [InlineKeyboardButton("ℹ️ Help", callback_data="help")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        message = await update.message.reply_text(
            "👋 Welcome to TradingBot! Choose an option:", reply_markup=reply_markup)

        chat_id = update.effective_chat.id
        user_data[chat_id] = default_state.copy()
        user_data[chat_id]["messages"] = [message.message_id]

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "ℹ️ Use /start to begin.\nChoose Buy/Sell, enter Symbol, Amount (USD), Stop Loss, Take Profit.\nConfirm before executing.")

    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        chat_id = query.message.chat_id

        if chat_id not in user_data:
            user_data[chat_id] = default_state.copy()

        user_data[chat_id]["messages"].append(query.message.message_id)

        if query.data in ("buy", "sell"):
            user_data[chat_id]["action"] = query.data
            user_data[chat_id]["order"] = {}
            user_data[chat_id]["step"] = "symbol"
            msg = await query.message.reply_text("📊 Enter the stock symbol (e.g., AAPL):")
            user_data[chat_id]["messages"].append(msg.message_id)

        elif query.data == "help":
            await query.message.reply_text(
                "💡 Send /start to start trading.\nUse Buy/Sell buttons to initiate an order.")

        elif query.data == "yes":
            await self.place_order(chat_id, context)

        elif query.data == "no":
            await query.message.reply_text("❌ Order cancelled.")
            await self.clean_messages(chat_id, context)

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        if chat_id != CHAT_ID:
            await update.message.reply_text("❌ Unauthorized access.")
            return

        text = update.message.text.strip().upper()

        if chat_id not in user_data:
            await update.message.reply_text("⚠️ Please press /start first.")
            return

        user_data[chat_id]["messages"].append(update.message.message_id)
        order = user_data[chat_id]["order"]
        step = user_data[chat_id]["step"]

        if step == "symbol":
            order["symbol"] = text
            user_data[chat_id]["step"] = "amount"
            msg = await update.message.reply_text("💰 Enter amount in USD:")
            user_data[chat_id]["messages"].append(msg.message_id)

        elif step == "amount":
            try:
                order["amount"] = float(text)
                user_data[chat_id]["step"] = "sl"
                msg = await update.message.reply_text("🔻 Enter Stop Loss % (e.g., 2 for 2%):")
                user_data[chat_id]["messages"].append(msg.message_id)
            except ValueError:
                await update.message.reply_text("❌ Invalid amount. Please enter a number.")

        elif step == "sl":
            try:
                order["sl"] = float(text)
                user_data[chat_id]["step"] = "tp"
                msg = await update.message.reply_text("🎯 Enter Take Profit % (e.g., 5 for 5%):")
                user_data[chat_id]["messages"].append(msg.message_id)
            except ValueError:
                await update.message.reply_text("❌ Invalid SL. Please enter a number.")

        elif step == "tp":
            try:
                order["tp"] = float(text)
                user_data[chat_id]["step"] = "confirm"
                await self.confirm_order(chat_id, context)
            except ValueError:
                await update.message.reply_text("❌ Invalid TP. Please enter a number.")

    async def confirm_order(self, chat_id, context):
        order = user_data[chat_id]["order"]
        action = user_data[chat_id]["action"]
        text = (
            f"📝 Confirm {action.upper()} Order:\n\n"
            f"📊 Symbol: {order['symbol']}\n"
            f"💰 Amount: ${order['amount']}\n"
            f"🔻 Stop Loss: {order['sl']}%\n"
            f"🎯 Take Profit: {order['tp']}%\n\n"
            f"✅ Proceed?"
        )
        keyboard = [[
            InlineKeyboardButton("✅ Yes", callback_data="yes"),
            InlineKeyboardButton("❌ No", callback_data="no")
        ]]
        markup = InlineKeyboardMarkup(keyboard)
        msg = await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=markup)
        user_data[chat_id]["messages"].append(msg.message_id)

    async def place_order(self, chat_id, context):
        order = user_data[chat_id]["order"]
        action = user_data[chat_id]["action"]
        symbol = order["symbol"]
        amount = order["amount"]

        try:
            details = self.ib.reqContractDetails(util.Stock(symbol, "SMART", "USD"))
            contract = details[0].contract
            self.ib.qualifyContracts(contract)

            ticker = self.ib.reqMktData(contract, '', False, False)
            await asyncio.sleep(2)
            price = ticker.marketPrice()
            if price <= 0:
                raise Exception("Market price not available")

            quantity = round(amount / price, 2)

            # Calculate SL and TP prices
            sl_pct = order['sl'] / 100
            tp_pct = order['tp'] / 100
            if action == 'buy':
                stop_price = price * (1 - sl_pct)
                take_profit_price = price * (1 + tp_pct)
            else:  # sell
                stop_price = price * (1 + sl_pct)
                take_profit_price = price * (1 - tp_pct)

            # Create bracket (OCO) order
            bracket = self.ib.bracketOrder(
                action.upper(), quantity,
                price,
                take_profit_price,
                stop_price
            )

            # Place all orders in the bracket
            for ord_ in bracket:
                self.ib.placeOrder(contract, ord_)

            await context.bot.send_message(
                chat_id=chat_id,
                text=(f"📤 Bracket order sent: {action.upper()} {quantity} {symbol} @ ${price:.2f}\n"
                      f"🎯 TP @ ${take_profit_price:.2f}, 🔻 SL @ ${stop_price:.2f}")
            )
            logger.info(f"✅ Bracket order executed: {action.upper()} {quantity} {symbol}")
        except Exception as e:
            await context.bot.send_message(chat_id=chat_id, text=f"❌ Order failed: {e}")
            logger.error(f"❌ Order failed: {e}")
        finally:
            await self.clean_messages(chat_id, context)

    async def clean_messages(self, chat_id, context):
        try:
            for msg_id in user_data[chat_id]["messages"]:
                await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
        except Exception as e:
            logger.warning(f"⚠️ Message cleanup failed: {e}")
        user_data[chat_id] = default_state.copy()

    async def run(self):
        try:
            await self.connect_ibkr()
        except Exception as e:
            logger.error(f"Failed to connect to IBKR: {e}")
            await self.app.bot.send_message(chat_id=CHAT_ID, text=f"❌ IBKR connection failed: {e}")
            return

        self.app.add_handler(CommandHandler("start", self.start))
        self.app.add_handler(CommandHandler("help", self.help_command))
        self.app.add_handler(CallbackQueryHandler(self.handle_callback))
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))

        logger.info("🚀 Bot is running!")
        await self.app.start()
        await self.app.updater.start_polling()
        await self.app.updater.idle()
        await self.disconnect_ibkr()


# ✅ Start bot
if __name__ == "__main__":
    bot = TradingBot()
    asyncio.run(bot.run())
