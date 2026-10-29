"""Final-2: independent paper account with two scale-ins and profit trailing."""
import argparse
import asyncio
import math
from binance_cascade_hunter_dynamic_v2_paper import V2PaperHunter
from binance_cascade_hunter_dynamic_v2 import log

ADD_LEVELS = (0.05, 0.10)
ADD_FRACTION = 0.50
TRAIL_ACTIVATION = 0.018
TRAIL_DISTANCE = 0.009
TARGET_STEP = 0.005


class Final2PaperHunter(V2PaperHunter):
    def __init__(self, state_path='paper_state_final2.json', fee_rate=0.0005,
                 reset=False, market_data=None):
        super().__init__(state_path, fee_rate, reset, market_data)

    def settings(self):
        return {**super().settings(), 'mode':'final2_scale_trail',
                'add_levels':list(ADD_LEVELS), 'add_fraction':ADD_FRACTION,
                'add_basis':'original_fill_notional', 'stop_basis':'original_signal_price',
                'trail_activation':TRAIL_ACTIVATION, 'trail_distance':TRAIL_DISTANCE,
                'target_step':TARGET_STEP}

    async def enter(self, symbol, side, price):
        if symbol in self.positions:
            return False
        if not await super().enter(symbol, side, price):
            return False
        p=self.positions[symbol]
        fill=self.exchange.positions[symbol]
        p.update(original_entry=fill['entry'], original_notional=fill['entry']*fill['amount'],
                 initial_sl=p['sl'], adds_done=0, trailing=False, best_price=fill['entry'],
                 entry=fill['entry'], amount=fill['amount'])
        p['tp']=p['entry']*(1+TRAIL_ACTIVATION if side=='buy' else 1-TRAIL_ACTIVATION)
        self.save()
        return True

    async def close_position(self, symbol, reason, trigger_price):
        p=self.positions[symbol]
        await self.exchange.create_order(symbol,'market','sell' if p['side']=='buy' else 'buy',
                                         p['amount'],params={'reduceOnly':True})
        self.record_exit_reason(symbol,reason,trigger_price)
        del self.positions[symbol]
        self.save()
        log.info('EXIT %s %s @ %.8f',reason,symbol,trigger_price)

    async def add_position(self,symbol,price):
        p=self.positions[symbol]
        amount=float(self.exchange.amount_to_precision(symbol,p['original_notional']*ADD_FRACTION/price))
        if amount<=0:
            return False
        # PaperExchange enforces market limits, free margin and fees before mutation.
        await self.exchange.create_order(symbol,'market',p['side'],amount,params={'scaleIn':True})
        fill=self.exchange.positions[symbol]
        p.update(entry=fill['entry'],amount=fill['amount'],adds_done=p['adds_done']+1)
        p['tp']=p['entry']*(1+TRAIL_ACTIVATION if p['side']=='buy' else 1-TRAIL_ACTIVATION)
        self.exchange.trades[-1].update(entry_reason='SCALE_IN',add_level=ADD_LEVELS[p['adds_done']-1],
                                       original_notional=p['original_notional'])
        self.save()
        log.info('SCALE IN %s level=%d average=%.8f',symbol,p['adds_done'],p['entry'])
        return True

    async def manage_exits(self):
        for symbol,p in list(self.positions.items()):
            try:
                price=float((await self.exchange.fetch_ticker(symbol))['last'])
                direction=1 if p['side']=='buy' else -1
                # Stops take priority over averaging, including price gaps past the stop.
                if direction*(price-p['sl'])<=0:
                    await self.close_position(symbol,'TRAIL_SL' if p['trailing'] else 'SL',price)
                    continue
                adverse=-direction*(price/p['original_entry']-1)
                if not p['trailing']:
                    while p['adds_done']<len(ADD_LEVELS) and adverse+1e-12>=ADD_LEVELS[p['adds_done']]:
                        if not await self.add_position(symbol,price):break
                    gain=direction*(price/p['entry']-1)
                    if gain+1e-12>=TRAIL_ACTIVATION:
                        p['trailing']=True
                        p['best_price']=price
                if p['trailing']:
                    p['best_price']=max(p['best_price'],price) if direction==1 else min(p['best_price'],price)
                    best=p['best_price']
                    candidate=best*(1-direction*TRAIL_DISTANCE)
                    p['sl']=max(p['sl'],candidate) if direction==1 else min(p['sl'],candidate)
                    # The TP becomes a moving objective; reaching it extends the run, not a forced exit.
                    gain=direction*(best/p['entry']-1)
                    steps=max(1,math.floor((gain-TRAIL_ACTIVATION)/TARGET_STEP)+1)
                    target=TRAIL_ACTIVATION+steps*TARGET_STEP
                    p['tp']=p['entry']*(1+direction*target)
                self.save()
            except Exception as error:
                log.warning('Position management failed %s: %s',symbol,error)
                self.save()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state',default='paper_state_final2.json')
    parser.add_argument('--reset',action='store_true')
    args=parser.parse_args()
    bot=Final2PaperHunter(args.state,reset=args.reset)
    log.warning('FINAL-2 PAPER: excludes liquidation, funding and slippage; averaging increases exposure')
    try:asyncio.run(bot.start())
    except KeyboardInterrupt:log.info('Final-2 stopped; state saved')


if __name__=='__main__':main()
