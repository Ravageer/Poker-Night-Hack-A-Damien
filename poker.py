import serial
import time
import threading
import random
from flask import Flask, render_template
from flask_socketio import SocketIO
from treys import Card, Evaluator

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*")
state_lock = threading.Lock()

try:
    arduino = serial.Serial('COM3', 9600, timeout=1)
    time.sleep(2) 
except:
    arduino = None
    print("Warning: Arduino not connected on COM3")

suits = ['♠', '♥', '♦', '♣']
ranks = ['2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K', 'A']

game_state = {
    "pot": 0,
    "community_cards": [],
    "stage": "Waiting", 
    "current_bet": 0,
    "players": {
        "HardwarePlayer": {"money": 1000, "cards": [], "status": "Active", "bet": 0},
        "Bot_1": {"money": 1000, "cards": [], "status": "Active", "bet": 0},
        "Bot_2": {"money": 1000, "cards": [], "status": "Active", "bet": 0}
    }
}


human_stats = {
    "hands_played": 0,
    "vpip_count": 0,  
    "raise_count": 0,
    "fold_count": 0,
    "faced_raise_count": 0,
    "folded_to_raise_count": 0
}

latest_encoder_pos = 0
hardware_clicked = False
game_started = False

def listen_to_arduino():
    global latest_encoder_pos, hardware_clicked
    while True:
        if arduino and arduino.in_waiting > 0:
            try:
                msg = arduino.readline().decode('utf-8').strip()
                if msg == "CLICK":
                    hardware_clicked = True
                elif msg.startswith("POS:"):
                    latest_encoder_pos = int(msg.split(":")[1])
                    socketio.emit('dial_update', {'pos': latest_encoder_pos})
            except:
                pass
        time.sleep(0.01)

def build_deck():
    return [f"{r}{s}" for s in suits for r in ranks]

def update_ui():
    socketio.emit('state_update', game_state)

def send_to_arduino(command):
    if arduino:
        arduino.write(f"{command}\n".encode())
        time.sleep(0.05) 

def to_treys_card(card_str):
    rank = card_str[:-1]
    suit = card_str[-1]
    if rank == '10': rank = 'T'
    suit_map = {'♠': 's', '♥': 'h', '♦': 'd', '♣': 'c'}
    return Card.new(f"{rank}{suit_map[suit]}")


def get_preflop_strength(hole_cards, position):
    """Chen Formula heavily modified by Positional Awareness."""
    if len(hole_cards) != 2: return 0.5
    
    rank_map = {'2':2,'3':3,'4':4,'5':5,'6':6,'7':7,'8':8,'9':9,'10':10,'J':11,'Q':12,'K':13,'A':14}
    r1, r2 = rank_map[hole_cards[0][:-1]], rank_map[hole_cards[1][:-1]]
    s1, s2 = hole_cards[0][-1], hole_cards[1][-1]
    
    high_card, low_card = max(r1, r2), min(r1, r2)
    
    score = high_card * 2.0
    if high_card == 14: score = 20.0 
    elif high_card == 13: score = 16.0 
    elif high_card == 12: score = 12.0 
    elif high_card == 11: score = 10.0 
    
    is_pair = (high_card == low_card)
    if is_pair: score = max(10.0, score * 2.0)
    if s1 == s2: score += 4.0
    
    gap = high_card - low_card
    if not is_pair:
        if gap == 1: score -= 2.0
        elif gap == 2: score -= 4.0
        elif gap == 3: score -= 8.0
        elif gap > 3: score -= 10.0
    
    if position == "early": score *= 0.85
    elif position == "late": score *= 1.25

    return max(0.0, min(1.0, (score + 10) / 50.0))

def get_hand_strength(hole_cards, community_cards, position="mid"):
    if len(community_cards) == 0:
        return get_preflop_strength(hole_cards, position)
    try:
        evaluator = Evaluator()
        hand = [to_treys_card(c) for c in hole_cards]
        board = [to_treys_card(c) for c in community_cards]
        score = evaluator.evaluate(board, hand)
        return max(0, min(1.0, 1.0 - (score / 7462.0)))
    except:
        return 0.5

def detect_draws(hole_cards, community_cards):
    """Calculates massive equity realization (OESD & Flush Draws)."""
    if len(community_cards) < 3: return False, False
    
    all_cards = hole_cards + community_cards
    suits_present = [c[-1] for c in all_cards]
    ranks_present = [c[:-1] for c in all_cards]
    
    rank_map = {'2':2,'3':3,'4':4,'5':5,'6':6,'7':7,'8':8,'9':9,'10':10,'J':11,'Q':12,'K':13,'A':14}
    rank_ints = sorted(list(set([rank_map[r] for r in ranks_present])))
    
    flush_draw = any(suits_present.count(s) == 4 for s in set(suits_present))
    
    straight_draw = False
    if len(rank_ints) >= 4:
        for i in range(len(rank_ints) - 3):
            if rank_ints[i+3] - rank_ints[i] == 3:
                straight_draw = True
                break
                
    return flush_draw, straight_draw

def analyze_board_texture(community_cards):
    """Detects highly coordinated boards."""
    if len(community_cards) < 3:
        return {"pair": False, "dangerous": False, "draw_heavy": False, "monotone": False}
    
    ranks_map = {'2':2,'3':3,'4':4,'5':5,'6':6,'7':7,'8':8,'9':9,'10':10,'J':11,'Q':12,'K':13,'A':14}
    ranks = [ranks_map.get(c[:-1], 0) for c in community_cards]
    suits = [c[-1] for c in community_cards]
    
    has_pair = len(set(ranks)) < len(ranks)
    flush_draw = any(suits.count(s) >= 2 for s in set(suits))
    monotone = any(suits.count(s) >= 3 for s in set(suits)) 
    
    # Gap analysis for straight danger
    sorted_ranks = sorted(list(set(ranks)))
    straight_danger = False
    if len(sorted_ranks) >= 3:
        for i in range(len(sorted_ranks) - 2):
            if sorted_ranks[i+2] - sorted_ranks[i] <= 4:
                straight_danger = True

    return {
        "dangerous": has_pair or straight_danger or monotone,
        "draw_heavy": flush_draw or straight_danger,
        "monotone": monotone,
        "pair": has_pair
    }

def get_position(bot_name):
    return "early" if bot_name == "Bot_1" else "late"

def get_bot_personality(bot_name):
    base_aggression = 0.70 if bot_name == "Bot_1" else 0.50
    base_tightness = 0.60 if bot_name == "Bot_1" else 0.65
    base_bluff = 0.40 if bot_name == "Bot_1" else 0.25
    
    
    if human_stats["hands_played"] > 5:
        vpip = human_stats["vpip_count"] / human_stats["hands_played"]
        # Exploit Nits / Maniacs
        if vpip < 0.25:
            base_bluff *= 1.4; base_aggression *= 1.2
        elif vpip > 0.60:
            base_tightness *= 1.3; base_bluff *= 0.5
            
        # Exploit Fold-to-Raise (FtR) 
        if human_stats["faced_raise_count"] > 3:
            ftr = human_stats["folded_to_raise_count"] / human_stats["faced_raise_count"]
            if ftr > 0.65:
                # Human folds to 3-bets too much. 
                base_bluff *= 1.5 
                base_aggression *= 1.3
            
    return {"aggression": min(1.0, base_aggression), "tightness": min(1.0, base_tightness), "bluff_freq": min(1.0, base_bluff)}

def get_gto_bet_size(pot, money, strength, aggression, is_draw):
    """Action Abstraction: Bots only pick from GTO standard sizes to stay unexploitable."""
    if pot == 0: pot = 40
    
    # Nodes: Small (33%), Medium (50%), Large (75%), Overbet (120%)
    nodes = [0.33, 0.50, 0.75, 1.20]
    
    if strength > 0.85:
        weights = [0.1, 0.2, 0.4, 0.3 * aggression] # Favors big bets
    elif strength > 0.65:
        weights = [0.2, 0.5, 0.3, 0.0] # Favors mid bets
    elif is_draw:
        weights = [0.1, 0.3, 0.6 * aggression, 0.0] # Semi-bluffs size up for fold equity
    else:
        weights = [0.6, 0.3, 0.1, 0.0] # Small stab

    fraction = random.choices(nodes, weights=weights)[0]
    bet = int(pot * fraction)
    bet = round(bet / 10) * 10
    return max(10, min(bet, money))



def bot_action(bot_name):
    with state_lock:
        bot = game_state["players"][bot_name]
        if bot["status"] in ["Folded", "All-In", "Bankrupt"]: return
        amount_to_call = game_state["current_bet"] - bot["bet"]
        cards = bot["cards"].copy()
        comm_cards = game_state["community_cards"].copy()
        money = bot["money"]
        stage = game_state["stage"]
        pot = game_state["pot"]

    time.sleep(1.5) 
    
    position = get_position(bot_name)
    hand_strength = get_hand_strength(cards, comm_cards, position)
    board_texture = analyze_board_texture(comm_cards)
    flush_draw, straight_draw = detect_draws(cards, comm_cards)
    personality = get_bot_personality(bot_name)
    
    # Stack-to-Pot Ratio (Pot Commitment)
    total_stack_start = money + bot["bet"]
    invested_ratio = 0 if total_stack_start == 0 else bot["bet"] / total_stack_start
    is_pot_committed = invested_ratio > 0.40
    
    # Equity Realization & Board Fear
    if stage in ["Flop", "Turn"]:
        if flush_draw or straight_draw: hand_strength = max(hand_strength, 0.75)
    
    if stage != "Pre-flop":
        if board_texture["monotone"] and not flush_draw and hand_strength < 0.9:
            hand_strength *= 0.65 # Terrified of 3-flush boards without a flush
        elif board_texture["dangerous"] and hand_strength < 0.8:
            hand_strength *= 0.85 

    with state_lock:
        if amount_to_call == 0:
            # Decide to Check or Bet
            bluff_roll = random.random() < personality["bluff_freq"]
            
            # Polarized River Bluffing
            if stage == "River" and hand_strength < 0.3 and bluff_roll:
                bet_size = get_gto_bet_size(pot, money, 0.99, personality["aggression"], False) # Size like the nuts
                print(f"🤖 {bot_name} Overbets ${bet_size} (Polarized Bluff).")
            elif hand_strength > 0.6 or (bluff_roll and hand_strength > 0.4):
                bet_size = get_gto_bet_size(pot, money, hand_strength, personality["aggression"], (flush_draw or straight_draw))
                
                if bet_size > 0:
                    bot["bet"] += bet_size; bot["money"] -= bet_size; game_state["pot"] += bet_size
                    game_state["current_bet"] = max(game_state["current_bet"], bot["bet"])
                    if bot["money"] == 0: bot["status"] = "All-In"
                    print(f"🤖 {bot_name} Bets ${bet_size}.")
                else:
                    print(f"🤖 {bot_name} Checks.")
            else:
                print(f"🤖 {bot_name} Checks.")
        else:
            # Facing a Bet
            pot_odds = (amount_to_call / (pot + amount_to_call)) * 100
            
            # GTO Math
            win_ev = hand_strength * pot
            fold_equity = min(1.0, 0.45 * personality["aggression"])
            fold_ev = fold_equity * (pot + amount_to_call)
            total_ev = (win_ev + fold_ev) - amount_to_call
            
            tight_mult = 1.0 + (personality["tightness"] - 0.5)
            
            if is_pot_committed and hand_strength > 0.55:
                # Refuse to fold top pair if committed
                actual_call = min(amount_to_call, money)
                bot["bet"] += actual_call; bot["money"] -= actual_call; game_state["pot"] += actual_call
                bot["status"] = "All-In" if bot["money"] == 0 else "Active"
                print(f"🤖 {bot_name} is Pot Committed. Calls ${actual_call}.")
                
            elif total_ev < (0 * tight_mult) and pot_odds > 20 and not (random.random() < personality["bluff_freq"] * 0.5):
                bot["status"] = "Folded"
                print(f"🤖 {bot_name} Folds.")
                
            elif hand_strength > 0.85 and personality["aggression"] > 0.5:
                # Standard 3-Bet / Value Raise
                raise_amount = min(int((amount_to_call * 2.5) + (pot * 0.3)), money)
                raise_amount = max(round(raise_amount / 10) * 10, amount_to_call * 2)
                
                bot["bet"] += raise_amount; bot["money"] -= raise_amount; game_state["pot"] += raise_amount
                game_state["current_bet"] = max(game_state["current_bet"], bot["bet"])
                bot["status"] = "All-In" if bot["money"] == 0 else "Active"
                print(f"🤖 {bot_name} RAISES to ${raise_amount}!")
                
            else:
                # Call
                actual_call = min(amount_to_call, money)
                bot["bet"] += actual_call; bot["money"] -= actual_call; game_state["pot"] += actual_call
                bot["status"] = "All-In" if bot["money"] == 0 else "Active"
                print(f"🤖 {bot_name} Calls ${actual_call}.")
                
    update_ui()


def player_turn():
    global hardware_clicked, latest_encoder_pos
    
    with state_lock:
        player = game_state["players"]["HardwarePlayer"]
        if player["status"] in ["Folded", "All-In", "Bankrupt"]: return
        amount_to_call = game_state["current_bet"] - player["bet"]
        player_money = player["money"]

    print(f"\n[Your Turn] Call: ${amount_to_call}")
    send_to_arduino("TURN:1") 
    
    hardware_clicked = False
    initial_dial_pos = latest_encoder_pos 
    last_handled_pos = initial_dial_pos
    
    bet_amount = amount_to_call 
    start_time = time.time()
    action_taken = False

    if amount_to_call > 0:
        human_stats["faced_raise_count"] += 1

    while time.time() - start_time < 15.0:
        if latest_encoder_pos != last_handled_pos:
            start_time = time.time() 
            clicks = latest_encoder_pos - initial_dial_pos
            
            if amount_to_call > 0: bet_amount = 0 if clicks < 0 else amount_to_call + (clicks * 10)
            else: bet_amount = max(0, clicks * 10) 
            
            if bet_amount >= player_money: bet_amount = player_money
                
            send_to_arduino(f"DISP:{player_money - bet_amount}")
            last_handled_pos = latest_encoder_pos
            
        if hardware_clicked:
            action_taken = True
            break
        time.sleep(0.05) 

    with state_lock:
        if action_taken:
            if bet_amount < amount_to_call and bet_amount < player_money:
                print("Fold Confirmed.")
                player["status"] = "Folded"
                human_stats["fold_count"] += 1
                if amount_to_call > 0: human_stats["folded_to_raise_count"] += 1
            else:
                if bet_amount == player_money and player_money > 0:
                    player["status"] = "All-In"
                    print(f"🔥 ALL-IN with ${bet_amount}!")
                else:
                    action_name = "Checks" if bet_amount == 0 else "Calls/Bets"
                    print(f"Action Confirmed: Player {action_name} ${bet_amount}")
                
                if bet_amount > 0: human_stats["vpip_count"] += 1
                if bet_amount > amount_to_call: human_stats["raise_count"] += 1
                    
                player["money"] -= bet_amount; player["bet"] += bet_amount; game_state["pot"] += bet_amount
                game_state["current_bet"] = max(game_state["current_bet"], player["bet"])
        else:
            print("Time's up! Auto-Folded.")
            player["status"] = "Folded"
            human_stats["fold_count"] += 1
            if amount_to_call > 0: human_stats["folded_to_raise_count"] += 1

    send_to_arduino(f"DISP:{player['money']}")
    send_to_arduino("TURN:0") 
    update_ui()


def count_active_players():
    return sum(1 for p in game_state["players"].values() if p["status"] not in ["Folded", "Bankrupt"])

def check_game_end():
    with state_lock:
        if count_active_players() == 1:
            for name, player in game_state["players"].items():
                if player["status"] not in ["Folded", "Bankrupt"]: return True, name
    return False, None

def run_game_loop():
    global hardware_clicked, game_started, human_stats
    
    print("\n🎰 WAITING FOR INITIAL CLICK TO START...")
    while not hardware_clicked: time.sleep(0.1)
    
    game_started = True
    socketio.emit('game_started')
    print("🎮 GAME STARTING!\n")
    hardware_clicked = False
    time.sleep(0.5)
    
    while True:
        human_stats["hands_played"] += 1
        print("\n NEW HAND ")
        deck = build_deck()
        random.shuffle(deck)
        
        with state_lock:
            game_state["community_cards"] = []
            game_state["pot"] = 0
            game_state["current_bet"] = 0
            for name, p in game_state["players"].items():
                p["status"] = "Active" if p["money"] > 0 else "Bankrupt"
                p["cards"] = [deck.pop(), deck.pop()]
                p["bet"] = 0
        
        update_ui()
        send_to_arduino(f"DISP:{game_state['players']['HardwarePlayer']['money']}")
        
        for stage in ["Pre-flop", "Flop", "Turn", "River"]:
            is_end, early_winner = check_game_end()
            if is_end: break

            with state_lock:
                game_state["stage"] = stage
                print(f"\nDealing {stage}...")
                if stage == "Flop": game_state["community_cards"].extend([deck.pop() for _ in range(3)])
                elif stage in ["Turn", "River"]: game_state["community_cards"].append(deck.pop())
                
            update_ui()
            player_turn()
            if check_game_end()[0]: break 
            bot_action("Bot_1")
            if check_game_end()[0]: break
            bot_action("Bot_2")

        with state_lock: game_state["stage"] = "Showdown"
        update_ui()
        print("\n--- SHOWDOWN ---")
        time.sleep(1)

        is_end, early_winner = check_game_end()
        winner_name = None
        
        if is_end:
            winner_name = early_winner
        else:
            evaluator = Evaluator()
            board = [to_treys_card(c) for c in game_state["community_cards"]]
            best_score = 9999 
            
            for name, player in game_state["players"].items():
                if player["status"] in ["Folded", "Bankrupt"]: continue
                hand = [to_treys_card(c) for c in player["cards"]]
                score = evaluator.evaluate(board, hand)
                hand_name = evaluator.class_to_string(evaluator.get_rank_class(score))
                print(f"{name} holds {player['cards']} -> {hand_name}")
                if score < best_score:
                    best_score = score
                    winner_name = name

        with state_lock:
            if winner_name:
                print(f"\n🏆 {winner_name} WINS THE POT OF ${game_state['pot']}! 🏆")
                game_state["players"][winner_name]["money"] += game_state["pot"]
                game_state["stage"] = f"{winner_name} Wins!"
            else:
                print("\nEveryone folded or went bankrupt.")
                game_state["stage"] = "No Winner"
            game_state["pot"] = 0
            
        send_to_arduino(f"DISP:{game_state['players']['HardwarePlayer']['money']}")
        update_ui()
        time.sleep(4) 
        
        if game_state["players"]["HardwarePlayer"]["money"] <= 0:
            print("\n💀 Game Over. 💀")
            with state_lock: game_state["stage"] = "Game Over (Click to Restart)"
            update_ui(); send_to_arduino("DISP:0")
            
            hardware_clicked = False
            while not hardware_clicked: time.sleep(0.1)
                
            print("\n🔄 Restarting...")
            with state_lock:
                for name in game_state["players"]:
                    game_state["players"][name]["money"] = 1000
                    game_state["players"][name]["status"] = "Active"
                human_stats = {k: 0 for k in human_stats} 
            send_to_arduino("DISP:1000"); update_ui(); time.sleep(1)

        elif game_state["players"]["Bot_1"]["money"] <= 0 and game_state["players"]["Bot_2"]["money"] <= 0:
            print("\n👑 YOU BANKRUPTED EVERYONE! 👑")
            with state_lock: game_state["stage"] = "YOU WIN! (Click to Restart)"
            update_ui()
            send_to_arduino(f"DISP:{game_state['players']['HardwarePlayer']['money']}")
            
            hardware_clicked = False
            while not hardware_clicked: time.sleep(0.1)
                
            print("\n🔄 Restarting the table...")
            with state_lock:
                for name in game_state["players"]:
                    game_state["players"][name]["money"] = 1000
                    game_state["players"][name]["status"] = "Active"
                human_stats = {k: 0 for k in human_stats} 
            send_to_arduino("DISP:1000"); update_ui(); time.sleep(1)

@app.route('/')
def index(): return render_template('index.html')

if __name__ == '__main__':
    threading.Thread(target=listen_to_arduino, daemon=True).start()
    threading.Thread(target=run_game_loop, daemon=True).start()
    socketio.run(app, debug=True, use_reloader=False, host='0.0.0.0', port=5000)