# Copyright 2021 Optiver Asia Pacific Pty. Ltd.
#
# This file is part of Ready Trader Go.
#
#     Ready Trader Go is free software: you can redistribute it and/or
#     modify it under the terms of the GNU Affero General Public License
#     as published by the Free Software Foundation, either version 3 of
#     the License, or (at your option) any later version.
#
#     Ready Trader Go is distributed in the hope that it will be useful,
#     but WITHOUT ANY WARRANTY; without even the implied warranty of
#     MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#     GNU Affero General Public License for more details.
#
#     You should have received a copy of the GNU Affero General Public
#     License along with Ready Trader Go.  If not, see
#     <https://www.gnu.org/licenses/>.
import asyncio
import itertools

import math

from typing import List

from ready_trader_go import BaseAutoTrader, Instrument, Lifespan, MAXIMUM_ASK, MINIMUM_BID, Side


LOT_SIZE = 10
POSITION_LIMIT = 100
TICK_SIZE_IN_CENTS = 100
MIN_BID_NEAREST_TICK = (MINIMUM_BID + TICK_SIZE_IN_CENTS) // TICK_SIZE_IN_CENTS * TICK_SIZE_IN_CENTS
MAX_ASK_NEAREST_TICK = MAXIMUM_ASK // TICK_SIZE_IN_CENTS * TICK_SIZE_IN_CENTS

SPREAD_LIMIT = 100
EXIT_LIMIT = -400



class AutoTrader(BaseAutoTrader):
    """Example Auto-trader.

    When it starts this auto-trader places ten-lot bid and ask orders at the
    current best-bid and best-ask prices respectively. Thereafter, if it has
    a long position (it has bought more lots than it has sold) it reduces its
    bid and ask prices. Conversely, if it has a short position (it has sold
    more lots than it has bought) then it increases its bid and ask prices.
    """

    def __init__(self, loop: asyncio.AbstractEventLoop, team_name: str, secret: str):
        """Initialise a new instance of the AutoTrader class."""
        super().__init__(loop, team_name, secret)
        self.order_ids = itertools.count(1)
        self.bids = set()
        self.asks = set()
        self.ask_id = self.ask_price = self.bid_id = self.bid_price = self.position = self.hedge = 0
        self.ticks = itertools.count(1)

        self.hbids = set()
        self.hasks = set()
        self.hask_id = self.hbid_id = 0

        self.fut_bid = 0
        self.fut_ask = 0
        self.etf_ask = 0
        self.etf_bid = 0

        self.etf_price = 0
        self.fut_price = 0

        self.spread = 0

        self.fut_etf_spread50 = []
        self.fut_etf_ratio = 1
        self.fut_etf_ratio_sum = 0
        self.fut_etf_ratio_avg = 0

        self.ratio50 = []
        self.sum_squares = 0
        self.ratio_sd = 0
        self.z_score = 0
        self.prev_z = 0

        self.open = 0
        self.unexposed = 0




        self.ready = False
    
    def etf_buy(self, volume):
        if self.bid_id == 0 and self.position + volume < POSITION_LIMIT:
                self.bid_id = next(self.order_ids)
                self.send_insert_order(self.bid_id, Side.BUY, self.etf_ask, volume, Lifespan.FAK)
                self.bids.add(self.bid_id)
    
    def etf_sell(self, volume):
        if self.ask_id == 0 and self.position - volume > -POSITION_LIMIT:
                self.ask_id = next(self.order_ids)
                self.send_insert_order(self.ask_id, Side.SELL, self.etf_bid, volume, Lifespan.FAK)
                self.asks.add(self.ask_id)

    def on_error_message(self, client_order_id: int, error_message: bytes) -> None:
        """Called when the exchange detects an error.

        If the error pertains to a particular order, then the client_order_id
        will identify that order, otherwise the client_order_id will be zero.
        """
        self.logger.warning("error with order %d: %s", client_order_id, error_message.decode())
        if client_order_id != 0 and (client_order_id in self.bids or client_order_id in self.asks):
            self.on_order_status_message(client_order_id, 0, 0, 0)

    def on_hedge_filled_message(self, client_order_id: int, price: int, volume: int) -> None:
        """Called when one of your hedge orders is filled.

        The price is the average price at which the order was (partially) filled,
        which may be better than the order's limit price. The volume is
        the number of lots filled at that price.
        """
        self.logger.info("received hedge filled for order %d with average price %d and volume %d", client_order_id,
                         price, volume)
        
        if volume == 0:
            if client_order_id in self.bids:
                self.position += volume
                if (self.hedge * -1) - volume > -POSITION_LIMIT:
                    self.hask_id = next(self.order_ids)
                    self.hasks.add(self.hask_id)
                    self.send_hedge_order(self.hask_id, Side.ASK, self.fut_bid, volume)
                # print(f'spread: {self.fut_bid - self.etf_ask}')

            elif client_order_id in self.asks:
                self.position -= volume
                if (self.hedge * -1) + volume < POSITION_LIMIT:
                    self.hbid_id = next(self.order_ids)
                    self.hbids.add(self.hbid_id)
                    self.send_hedge_order(self.hbid_id, Side.BID, self.fut_ask, volume)
                # print(f'spread: {self.etf_bid - self.fut_ask}')

        if client_order_id in self.hbids:
            self.hedge -= volume
        elif client_order_id in self.hasks:
            self.hedge += volume

        # print(self.position, self.hedge)

    def on_order_book_update_message(self, instrument: int, sequence_number: int, ask_prices: List[int],
                                     ask_volumes: List[int], bid_prices: List[int], bid_volumes: List[int]) -> None:
        """Called periodically to report the status of an order book.

        The sequence number can be used to detect missed or out-of-order
        messages. The five best available ask (i.e. sell) and bid (i.e. buy)
        prices are reported along with the volume available at each of those
        price levels.
        """
        self.logger.info("received order book for instrument %d with sequence number %d", instrument,
                         sequence_number)


        
        if instrument == Instrument.FUTURE:
            self.fut_bid = bid_prices[0]
            self.fut_ask = ask_prices[0]
            self.fut_price = (self.fut_ask + self.fut_bid) / 2

        if instrument == Instrument.ETF:
            self.etf_ask = ask_prices[0]
            self.etf_bid = bid_prices[0]
            self.etf_price = (self.etf_ask + self.etf_bid) / 2

        # elif self.etf_ask < self.fut_bid:
        #     self.etf_buy(4)
        # elif self.etf_bid > self.fut_ask:
        #     self.etf_sell(4)
        

        
        '''calculate the price ratios'''
        if self.etf_price != 0:
            self.fut_etf_ratio = self.fut_price/self.etf_price
            self.fut_etf_ratio_sum += self.fut_etf_ratio
            self.fut_etf_spread50.append(self.fut_etf_ratio)
            if len(self.fut_etf_spread50) > 50:
                self.ready = True
                self.fut_etf_ratio_sum -= self.fut_etf_spread50.pop(0)

        if self.ready:
            square = (self.fut_etf_ratio - self.fut_etf_ratio_avg) ** 2
            self.sum_squares += square
            self.ratio50.append(square)
            if len(self.ratio50) > 50:
                self.sum_squares -= self.ratio50.pop(0)
            
            
            self.ratio_sd = math.sqrt(self.sum_squares / 50)
            self.z_score = (self.fut_etf_ratio - self.fut_etf_ratio_avg) / self.ratio_sd

        self.fut_etf_ratio_avg = self.fut_etf_ratio_sum / 50

        # print(f' avg: {self.fut_etf_ratio_avg} ratio: {self.fut_etf_ratio} sd: {self.ratio_sd} zscore: {self.z_score}')
        # print(f'zscore: {self.z_score} sumsquares: {self.sum_squares}')

        '''Short when z-score above 1 and long when z-score below 1.'''
        #right now, I think it's losing money on trades where the prices are near the convergence but the ask/bid spread is negative, so it trades at a loss

        if self.ready and self.z_score > 1.75 and self.etf_ask < self.fut_bid:
            self.etf_buy(30)
        elif self.ready and self.z_score < -1.75 and self.etf_ask < self.fut_bid and self.position > 0:
            self.etf_sell(30)
        elif self.ready and self.z_score > 1.75 and self.etf_bid > self.fut_ask and self.position < 0:
            self.etf_buy(30)
        elif self.ready and self.z_score < -1.75 and self.etf_bid > self.fut_ask:
            self.etf_sell(30)
        
        elif self.ready and self.z_score > 1.25 and self.etf_ask < self.fut_bid:
            self.etf_buy(20)
        elif self.ready and self.z_score < -1.25 and self.etf_ask < self.fut_bid and self.position > 0:
            self.etf_sell(20)
        elif self.ready and self.z_score > 1.25 and self.etf_bid > self.fut_ask and self.position < 0:
            self.etf_buy(20)
        elif self.ready and self.z_score < -1.25 and self.etf_bid > self.fut_ask:
            self.etf_sell(20)
        
        elif self.ready and self.z_score > 0.75 and self.etf_ask < self.fut_bid:
            self.etf_buy(10)
        elif self.ready and self.z_score < -0.75 and self.etf_ask < self.fut_bid and self.position > 0:
            self.etf_sell(10)
        elif self.ready and self.z_score > 0.75 and self.etf_bid > self.fut_ask and self.position < 0:
            self.etf_buy(10)
        elif self.ready and self.z_score < -0.75 and self.etf_bid > self.fut_ask:
            self.etf_sell(10)
        
        # # elif self.ready and self.z_score > 0.5 and self.etf_ask < self.fut_bid:
        # #     self.etf_buy(5)
        # # elif self.ready and self.z_score < -0.5 and self.etf_ask < self.fut_bid and self.position > 0:
        # #     self.etf_sell(5)
        # # elif self.ready and self.z_score > 0.5 and self.etf_bid > self.fut_ask and self.position < 0:
        # #     self.etf_buy(5)
        # # elif self.ready and self.z_score < 0.5 and self.etf_bid > self.fut_ask:
        # #     self.etf_sell(5)
        




        # # elif self.ready and self.z_score > self.prev_z and self.z_score > 0.5:
        # #     self.etf_buy(1)

        # # elif self.ready and self.z_score < self.prev_z and self.z_score < -0.5:
        # #     self.etf_sell(1)



        
        

        # self.prev_z = self.z_score



    def on_order_filled_message(self, client_order_id: int, price: int, volume: int) -> None:
        """Called when one of your orders is filled, partially or fully.

        The price is the price at which the order was (partially) filled,
        which may be better than the order's limit price. The volume is
        the number of lots filled at that price.
        """
        self.logger.info("received order filled for order %d with price %d and volume %d", client_order_id,
                         price, volume)
        if client_order_id in self.bids:
            self.position += volume
            if (self.hedge * -1) - volume > -POSITION_LIMIT:
                self.hask_id = next(self.order_ids)
                self.hasks.add(self.hask_id)
                self.send_hedge_order(self.hask_id, Side.ASK, self.fut_bid, volume)
            # print(f'spread: {self.fut_bid - self.etf_ask}')

        elif client_order_id in self.asks:
            self.position -= volume
            if (self.hedge * -1) + volume < POSITION_LIMIT:
                self.hbid_id = next(self.order_ids)
                self.hbids.add(self.hbid_id)
                self.send_hedge_order(self.hbid_id, Side.BID, self.fut_ask, volume)
            # print(f'spread: {self.etf_bid - self.fut_ask}')
            

    def on_order_status_message(self, client_order_id: int, fill_volume: int, remaining_volume: int,
                                fees: int) -> None:
        """Called when the status of one of your orders changes.

        The fill_volume is the number of lots already traded, remaining_volume
        is the number of lots yet to be traded and fees is the total fees for
        this order. Remember that you pay fees for being a market taker, but
        you receive fees for being a market maker, so fees can be negative.

        If an order is cancelled its remaining volume will be zero.
        """
        self.logger.info("received order status for order %d with fill volume %d remaining %d and fees %d",
                         client_order_id, fill_volume, remaining_volume, fees)
        if remaining_volume == 0:
            if client_order_id == self.bid_id:
                self.bid_id = 0
            elif client_order_id == self.ask_id:
                self.ask_id = 0

            # It could be either a bid or an ask
            self.bids.discard(client_order_id)
            self.asks.discard(client_order_id)


    def on_trade_ticks_message(self, instrument: int, sequence_number: int, ask_prices: List[int],
                               ask_volumes: List[int], bid_prices: List[int], bid_volumes: List[int]) -> None:
        """Called periodically when there is trading activity on the market.

        The five best ask (i.e. sell) and bid (i.e. buy) prices at which there
        has been trading activity are reported along with the aggregated volume
        traded at each of those price levels.

        If there are less than five prices on a side, then zeros will appear at
        the end of both the prices and volumes arrays.
        """
        self.logger.info("received trade ticks for instrument %d with sequence number %d", instrument,
                         sequence_number)
                
        


    

        

        
        
