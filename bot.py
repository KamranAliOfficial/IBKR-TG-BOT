import os
import asyncio
import logging
import nest_asyncio

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters
)
from ib_insync import IB, Stock

nest_asyncio.apply()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = "7537301802:AAHNMUItC6y8PWEICOwt2JxlmvPVJVuOkbs"
CHAT_ID = 6409841008

def make_state():
    return {"messages": [], "order": {}, "step": None, "action": None}

user_data = {}

class TradingBot:
    def __init__(self):
        self.app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
        self.ib = IB()

    def connect_ibkr(self):
        self.ib.connect("127.0.0.1", 7497, clientId=1)
        logger.info("✅ Connected to IBKR")

    def disconnect_ibkr(self):
        self.ib.disconnect()
        logger.info("🔌 Disconnected IBKR")

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        kb = [
            [InlineKeyboardButton("📈 Buy", callback_data="buy"),
             InlineKeyboardButton("📉 Sell", callback_data="sell")],
            [InlineKeyboardButton("ℹ️ Help", callback_data="help")]
        ]
        msg = await update.message.reply_text(
            "👋 Welcome! Choose an option:", 
            reply_markup=InlineKeyboardMarkup(kb)
        )
        cid = update.effective_chat.id
        user_data[cid] = make_state()
        user_data[cid]["messages"] = [msg.message_id]

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "ℹ️ /start → Buy/Sell → Symbol → Amount → SL% → TP% → Confirm"
        )

    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query
        cid = q.message.chat.id

        try:
            await q.answer()
        except:
            pass

        if cid not in user_data:
            user_data[cid] = make_state()
        user_data[cid]["messages"].append(q.message.message_id)

        if q.data in ("buy", "sell"):
            user_data[cid].update({
                "action": q.data,
                "order": {},
                "step": "symbol"
            })
            m = await q.message.reply_text("📊 Enter symbol (e.g., AAPL):")
            user_data[cid]["messages"].append(m.message_id)

        elif q.data == "help":
            await q.message.reply_text("ℹ️ Press /start to begin trading.")

        elif q.data == "yes":
            if user_data[cid].get("step") == "confirm":
                await self.place_order(cid, context)
            else:
                await q.message.reply_text("⚠️ Nothing to confirm.")

        elif q.data == "no":
            await q.message.reply_text("❌ Order cancelled.")
            await self.clean_messages(cid, context)

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        cid = update.effective_chat.id
        if cid != CHAT_ID:
            return await update.message.reply_text("❌ Unauthorized.")

        text = update.message.text.strip().upper()
        if cid not in user_data or not user_data[cid]["step"]:
            return await update.message.reply_text("⚠️ Use /start first.")

        user_data[cid]["messages"].append(update.message.message_id)
        sd = user_data[cid]
        o = sd["order"]

        if sd["step"] == "symbol":
            o["symbol"] = text
            sd["step"] = "amount"
            m = await update.message.reply_text("💰 Enter amount in USD:")
            sd["messages"].append(m.message_id)

        elif sd["step"] == "amount":
            try:
                o["amount"] = float(text)
                sd["step"] = "sl"
                m = await update.message.reply_text("🔻 Enter SL%:")
                sd["messages"].append(m.message_id)
            except ValueError:
                await update.message.reply_text("❌ Invalid number.")

        elif sd["step"] == "sl":
            try:
                o["sl"] = float(text)
                sd["step"] = "tp"
                m = await update.message.reply_text("🎯 Enter TP%:")
                sd["messages"].append(m.message_id)
            except ValueError:
                await update.message.reply_text("❌ Invalid number.")

        elif sd["step"] == "tp":
            try:
                o["tp"] = float(text)
                sd["step"] = "confirm"
                await self.confirm_order(cid, context)
            except ValueError:
                await update.message.reply_text("❌ Invalid number.")

    async def confirm_order(self, cid, context):
        o = user_data[cid]["order"]
        act = user_data[cid]["action"]
        text = (
            f"📝 Confirm {act.upper()}:\n"
            f"Symbol: {o['symbol']}\n"
            f"Amt: ${o['amount']}\n"
            f"SL: {o['sl']}%  TP: {o['tp']}%\n\n"
            "Proceed?"
        )
        kb = [[
            InlineKeyboardButton("✅ Yes", callback_data="yes"),
            InlineKeyboardButton("❌ No", callback_data="no")
        ]]
        m = await context.bot.send_message(
            cid, text, reply_markup=InlineKeyboardMarkup(kb)
        )
        user_data[cid]["messages"].append(m.message_id)

    async def place_order(self, cid, context):
        sd = user_data[cid]
        o = sd["order"]
        act, sym, amt = sd["action"], o["symbol"], o["amount"]

        try:
            details = self.ib.reqContractDetails(Stock(sym, "SMART", "USD"))
            if not details:
                raise Exception("No contract details")
            contract = details[0].contract
            self.ib.qualifyContracts(contract)

            test_price = os.getenv("TEST_PRICE")
            if test_price:
                price = float(test_price)
            else:
                ticker = self.ib.reqMktData(contract, "", False, False)
                price = None
                for _ in range(10):
                    await asyncio.sleep(0.5)
                    price = ticker.last or ticker.ask or ticker.bid
                    if price and price > 0:
                        break
                if not price or price <= 0:
                    bars = self.ib.reqHistoricalData(
                        contract, endDateTime="", durationStr="1 D",
                        barSizeSetting="1 min", whatToShow="TRADES",
                        useRTH=False, formatDate=1
                    )
                    if not bars:
                        raise Exception("No market data")
                    price = bars[-1].close

            qty = round(amt / price, 4)
            if qty <= 0:
                raise Exception(f"Bad qty: {qty}")

            slp, tpp = o["sl"] / 100, o["tp"] / 100
            if act == "buy":
                stop_price = price * (1 - slp)
                tp_price   = price * (1 + tpp)
            else:
                stop_price = price * (1 + slp)
                tp_price   = price * (1 - tpp)

            bracket = self.ib.bracketOrder(act.upper(), qty, price, tp_price, stop_price)
            for ord_ in bracket:
                self.ib.placeOrder(contract, ord_)

            await context.bot.send_message(
                cid,
                f"📤 Bracket order sent: {act.upper()} {qty} {sym} @ ${price:.2f}\n"
                f"🎯 TP @ ${tp_price:.2f}  🔻 SL @ ${stop_price:.2f}"
            )
        except Exception as e:
            await context.bot.send_message(cid, f"❌ Order failed: {e}")
            logger.error("Order failed", exc_info=True)
        finally:
            await self.clean_messages(cid, context)

    async def clean_messages(self, cid, context):
        for mid in user_data[cid]["messages"]:
            try:
                await context.bot.delete_message(chat_id=cid, message_id=mid)
            except:
                pass
        user_data[cid] = make_state()

    def run(self):
        self.connect_ibkr()
        self.app.add_handler(CommandHandler("start", self.start))
        self.app.add_handler(CommandHandler("help", self.help_command))
        self.app.add_handler(CallbackQueryHandler(self.handle_callback))
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))

        logger.info("🚀 Bot running (polling)...")
        self.app.run_polling()
        self.disconnect_ibkr()


if __name__ == "__main__":
    TradingBot().run()
