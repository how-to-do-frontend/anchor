
from . import DefaultResponsePacket as ResponsePacket
from . import DefaultRequestPacket as RequestPacket

from ..common.database.repositories import beatmaps, scores, relationships
from ..common.database.objects import DBBeatmap
from ..objects.player import Player
from .. import session

from ..common.objects import (
    BeatmapInfoRequest,
    ReplayFrameBundle,
    BeatmapInfoReply,
    StatusUpdate,
    BeatmapInfo,
    Message
)

from ..common.constants import (
    PresenceFilter,
    ClientStatus,
    Grade
)

from typing import Callable, Tuple, List

def register(packet: RequestPacket) -> Callable:
    def wrapper(func) -> Callable:
        session.handlers[packet] = func
        return func

    return wrapper

@register(RequestPacket.PONG)
def pong(player: Player):
    pass

@register(RequestPacket.EXIT)
def exit(player: Player, updating: bool):
    player.update_activity()

@register(RequestPacket.RECEIVE_UPDATES)
def receive_updates(player: Player, filter: PresenceFilter):
    player.filter = filter

@register(RequestPacket.PRESENCE_REQUEST)
def presence_request(player: Player, players: List[int]):
    for id in players:
        if not (target := session.players.by_id(id)):
            continue

        player.enqueue_presence(target)

@register(RequestPacket.STATS_REQUEST)
def stats_request(player: Player, players: List[int]):
    for id in players:
        if not (target := session.players.by_id(id)):
            continue

        player.enqueue_stats(target)

@register(RequestPacket.CHANGE_STATUS)
def change_status(player: Player, status: StatusUpdate):
    player.status.checksum = status.beatmap_checksum
    player.status.beatmap = status.beatmap_id
    player.status.action = status.action
    player.status.mods = status.mods
    player.status.mode = status.mode
    player.status.text = status.text

    # TODO: Update rank

    player.update_activity()

    # (This needs to be done for older clients)
    session.players.send_stats(player)

@register(RequestPacket.REQUEST_STATUS)
def request_status(player: Player):
    player.enqueue_stats(player)
    # TODO: Update rank

@register(RequestPacket.JOIN_CHANNEL)
def handle_channel_join(player: Player, channel_name: str):
    if channel_name == '#spectator':
        if player.spectating:
            channel = player.spectating.spectator_chat
        else:
            channel = player.spectator_chat
    else:
        channel = session.channels.by_name(channel_name)

    # TODO: Multiplayer channels

    if not channel:
        player.revoke_channel(channel_name)
        return

    channel.add(player)

@register(RequestPacket.LEAVE_CHANNEL)
def handle_channel_leave(player: Player, channel_name: str, kick: bool = False):
    if channel_name == '#spectator':
        if player.spectating:
            channel = player.spectating.spectator_chat
        else:
            channel = player.spectator_chat
    else:
        channel = session.channels.by_name(channel_name)

    # TODO: Multiplayer channels

    if not channel:
        player.revoke_channel(channel_name)
        return

    if kick:
        player.revoke_channel(channel_name)

    channel.remove(player)

@register(RequestPacket.SEND_MESSAGE)
def send_message(player: Player, message: Message):
    if message.target == '#spectator':
        if player.spectating:
            channel = player.spectating.spectator_chat
        else:
            channel = player.spectator_chat
    else:
        channel = session.channels.by_name(message.target)

    if not channel:
        player.revoke_channel(message.target)
        return

    player.update_activity()

    # TODO: Multiplayer channels
    # TODO: Submit message to datanase
    # TODO: Commands

    channel.send_message(player, message.content)

@register(RequestPacket.SEND_PRIVATE_MESSAGE)
def send_private_message(sender: Player, message: Message):
    if not (target := session.players.by_name(message.target)):
        sender.revoke_channel(message.target)
        return

    if sender.silenced:
        return

    if target.silenced:
        # TODO: Enqueue target silenced
        return

    if target.client.friendonly_dms:
        if sender.id not in target.friends:
            sender.enqueue_blocked_dms(sender.name)
            return

    # Limit message size
    if len(message.content) > 512:
        message.content = message.content[:512] + '... (truncated)'

    sender.logger.info(f'[PM -> {target.name}]: {message.content}')
    sender.update_activity()

    # TODO: Submit to database
    # TODO: Check commands

    if target.status.action == ClientStatus.Afk and target.away_message:
        sender.enqueue_message(
            Message(
                target.name,
                target.away_message,
                target.name,
                target.id
            )
        )
        return

    target.enqueue_message(
        Message(
            sender.name,
            message.content,
            sender.name,
            sender.id
        )
    )

@register(RequestPacket.ADD_FRIEND)
def add_friend(player: Player, target_id: int):
    if not (target := session.players.by_id(target_id)):
        return

    if target.id in player.friends:
        return

    relationships.create(
        player.id,
        target.id
    )

    session.logger.info(f'{player.name} is now friends with {target.name}.')

    player.reload_object()
    player.enqueue_friends()

@register(RequestPacket.REMOVE_FRIEND)
def remove_friend(player: Player, target_id: int):
    if not (target := session.players.by_id(target_id)):
        return

    if target.id not in player.friends:
        return

    relationships.delete(
        player.id,
        target.id
    )

    session.logger.info(f'{player.name} no longer friends with {target.name}.')

    player.reload_object()
    player.enqueue_friends()

@register(RequestPacket.BEATMAP_INFO)
def beatmap_info(player: Player, info: BeatmapInfoRequest):
    maps: List[Tuple[int, DBBeatmap]] = []

    # Fetch all matching beatmaps from database

    for index, filename in enumerate(info.filenames):
        if not (beatmap := beatmaps.fetch_by_file(filename)):
            continue

        maps.append((
            index,
            beatmap
        ))

    for id in info.beatmap_ids:
        if not (beatmap := beatmaps.fetch_by_id(id)):
            continue

        maps.append((
            -1,
            beatmap
        ))

    # Create beatmap response

    map_infos: List[BeatmapInfo] = []

    for index, beatmap in maps:
        ranked = {
            -2: 0, # Graveyard: Pending
            -1: 0, # WIP: Pending
             0: 0, # Pending: Pending
             1: 1, # Ranked: Ranked
             2: 2, # Approved: Approved
             3: 2, # Qualified: Approved
             4: 2, # Loved: Approved
        }[beatmap.status]

        # Get personal best in every mode for this beatmap
        grades = {
            0: Grade.N,
            1: Grade.N,
            2: Grade.N,
            3: Grade.N
        }

        for mode in range(4):
            personal_best = scores.fetch_personal_best(
                beatmap.id,
                player.id,
                mode
            )

            if personal_best:
                grades[mode] = Grade[personal_best.grade]

        map_infos.append(
            BeatmapInfo(
                index,
                beatmap.id,
                beatmap.set_id,
                beatmap.set_id, # thread_id
                ranked,
                grades[0],
                grades[1],
                grades[2],
                grades[3],
                beatmap.md5
            )
        )

    player.send_packet(
        ResponsePacket.BEATMAP_INFO_REPLY,
        BeatmapInfoReply(map_infos)
    )

@register(RequestPacket.START_SPECTATING)
def start_spectating(player: Player, player_id: int):
    if not (target := session.players.by_id(player_id)):
        return

    if target.id == session.bot_player.id:
        return

    # TODO: Check osu! mania support

    if (player.spectating) or (player in target.spectators):
        stop_spectating(player)
        return

    player.spectating = target

    # Join their channel
    player.enqueue_channel(target.spectator_chat)
    target.spectator_chat.add(player)

    # Enqueue to others
    for p in target.spectators:
        p.enqueue_fellow_spectator(player.id)

    # Enqueue to target
    target.spectators.append(player)
    target.enqueue_spectator(player.id)
    target.enqueue_channel(target.spectator_chat)

    # Check if target joined #spectator
    if target not in target.spectator_chat.users:
        target.spectator_chat.add(target)

@register(RequestPacket.STOP_SPECTATING)
def stop_spectating(player: Player):
    if not player.spectating:
        return

    # Leave spectator channel
    player.spectating.spectator_chat.remove(player)

    # Remove from target
    player.spectating.spectators.remove(player)

    # Enqueue to others
    for p in player.spectating.spectators:
        p.enqueue_fellow_spectator_left(player.id)

    # Enqueue to target
    player.spectating.enqueue_spectator_left(player.id)

    # If target has no spectators anymore
    # kick them from the spectator channel
    if not player.spectating.spectators:
        player.spectating.spectator_chat.remove(
            player.spectating
        )

    player.spectating = None

@register(RequestPacket.CANT_SPECTATE)
def cant_spectate(player: Player):
    if not player.spectating:
        return

    player.spectating.enqueue_cant_spectate(player.id)

    for p in player.spectating.spectators:
        p.enqueue_cant_spectate(player.id)

@register(RequestPacket.SEND_FRAMES)
def send_frames(player: Player, bundle: ReplayFrameBundle):
    if not player.spectators:
        return

    # TODO: Check osu! mania support

    for p in player.spectators:
        p.enqueue_frames(bundle)
