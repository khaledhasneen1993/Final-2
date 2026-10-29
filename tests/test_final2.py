import tempfile
import unittest
from pathlib import Path
from cascade_final2_paper import Final2PaperHunter
from fixtures import DynamicPublicData

S='BTC/USDT:USDT'
class Final2Tests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.t=tempfile.TemporaryDirectory();self.addCleanup(self.t.cleanup)
        self.path=Path(self.t.name)/'state.json';self.data=DynamicPublicData()
        self.bot=Final2PaperHunter(self.path,fee_rate=0,market_data=self.data)
        await self.bot.enter(S,'buy',100)
    async def price(self,n):
        self.data.price=n;await self.bot.manage_exits()
    async def test_first_add_half_original_notional(self):
        await self.price(95)
        p=self.bot.positions[S]
        self.assertEqual(p['adds_done'],1)
        self.assertAlmostEqual(self.bot.exchange.trades[-1]['price']*self.bot.exchange.trades[-1]['amount'],250,delta=.1)
        self.assertEqual(p['sl'],85)
        self.assertEqual(len(self.bot.positions),1)
    async def test_two_adds_not_repeated(self):
        await self.price(95);await self.price(90);await self.price(90)
        self.assertEqual(self.bot.positions[S]['adds_done'],2)
        self.assertEqual(len(self.bot.exchange.trades),3)
        self.assertAlmostEqual(self.bot.exchange.used,20,delta=.01)
    async def test_gap_past_both_levels(self):
        await self.price(89)
        self.assertEqual(self.bot.positions[S]['adds_done'],2)
    async def test_stop_before_averaging_on_gap(self):
        await self.price(80)
        self.assertFalse(self.bot.positions)
        self.assertEqual(len(self.bot.exchange.trades),2)
        self.assertEqual(self.bot.exchange.trades[-1]['exit_reason'],'SL')
    async def test_averaged_stop_remains_original(self):
        await self.price(95);await self.price(90);await self.price(85)
        self.assertFalse(self.bot.positions)
        self.assertEqual(self.bot.exchange.trades[-1]['exit_reason'],'SL')
    async def test_trail_arms_then_locks_and_closes(self):
        await self.price(101.8)
        p=self.bot.positions[S]
        self.assertTrue(p['trailing']);self.assertGreater(p['sl'],100)
        self.assertGreater(p['tp'],101.8)
        await self.price(105);stop=p['sl']
        await self.price(104.5);self.assertEqual(p['sl'],stop)
        await self.price(104)
        self.assertFalse(self.bot.positions)
        self.assertEqual(self.bot.exchange.trades[-1]['exit_reason'],'TRAIL_SL')
        self.assertGreater(self.bot.exchange.balance,100)
    async def test_restore_does_not_duplicate_add(self):
        await self.price(95)
        self.bot=Final2PaperHunter(self.path,fee_rate=0,market_data=self.data)
        await self.price(95)
        self.assertEqual(len(self.bot.exchange.trades),2)
        self.assertEqual(self.bot.positions[S]['adds_done'],1)
    async def test_restore_trailing_best(self):
        await self.price(103)
        stop=self.bot.positions[S]['sl']
        self.bot=Final2PaperHunter(self.path,fee_rate=0,market_data=self.data)
        await self.price(102.5)
        self.assertEqual(self.bot.positions[S]['sl'],stop)
    async def test_insufficient_equity_does_not_mark_added(self):
        self.bot.exchange.balance=20
        await self.price(95)
        self.assertEqual(self.bot.positions[S]['adds_done'],0)
        self.assertEqual(len(self.bot.exchange.trades),1)
    async def test_short_adds_and_trailing(self):
        await self.bot.close_position(S,'TEST',100)
        self.bot.last_trade.clear()
        await self.bot.enter(S,'sell',100)
        await self.price(105);await self.price(110)
        p=self.bot.positions[S]
        self.assertEqual(p['adds_done'],2);self.assertAlmostEqual(p['sl'],115)
        await self.price(p['entry']*.98)
        self.assertTrue(p['trailing']);self.assertLess(p['sl'],p['entry'])
        stop=p['sl'];await self.price(stop+1)
        self.assertFalse(self.bot.positions)
    async def test_all_fees_and_weighted_pnl(self):
        self.bot=Final2PaperHunter(Path(self.t.name)/'fee.json',market_data=self.data)
        await self.bot.enter(S,'buy',100)
        await self.price(95);await self.price(90)
        positions=self.bot.exchange.positions[S].copy()
        await self.price(85)
        fees=sum(t['fee_usdt'] for t in self.bot.exchange.trades)
        pnl=(85-positions['entry'])*positions['amount']
        self.assertAlmostEqual(self.bot.exchange.balance,100+pnl-fees)
    async def test_reject_v2_state(self):
        state=self.path.read_text();self.path.write_text(state.replace('final2_scale_trail','dynamic_v2_continuous'))
        with self.assertRaises(ValueError):Final2PaperHunter(self.path,fee_rate=0,market_data=self.data)
