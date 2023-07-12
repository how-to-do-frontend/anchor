
from .b20130329 import b20130329

from threading import Thread

class b20121223(b20130329):

    protocol_version = 13

    def enqueue_players(self, players):
        def enqueue(players):
            for player in players:
                player.update_rank()
                self.enqueue_presence(player)

        Thread(
            target=enqueue,
            args=[players],
            daemon=True
        ).start()

    def enqueue_player(self, player):
        player.update_rank()
        self.enqueue_presence(player)
        self.enqueue_stats(player)
