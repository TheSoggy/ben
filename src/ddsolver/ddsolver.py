import sys
import os
import ctypes
import multiprocessing
import pickle
from typing import Dict, List
from collections import Counter
from objects import Card
from ddsolver import dds
from colorama import Fore, Back, Style, init
from nn.timing import ModelTimer

init()

class DDSolver:

    # Default for dds_mode changed to 1
    # Transport table will be reused if same trump suit and the same or nearly the same cards distribution, deal.first can be different.
    # Always search to find the score. Even when the hand to play has only one card, with possible equivalents, to play.
    # If zero, we not always find the score
    # If 2 transport tables ignore trump

    def __init__(self, dds_mode=1, max_threads=0, verbose=False):
        # The number of threads is automatically configured by DDS on Windows,
        # taking into account the number of processor cores and available memory.
        # On Linux/Mac, SetMaxThreads should always be called (0 = auto-configure).
        dds.SetMaxThreads(max_threads)
        if verbose:
            sys.stderr.write(f"DDSolver being loaded version 2.9.0 - dds mode {dds_mode} - max threads {max_threads}\n")
        self.dds_mode = dds_mode

    def version(self):  
        return "2.9.0"
    
    def calculatepar(self, hand, vuln, print_result=True):
        with ModelTimer.time_call('dds_par'):
            return self._calculatepar_impl(hand, vuln, print_result)

    def _calculatepar_impl(self, hand, vuln, print_result=True):
        tableDealPBN = dds.ddTableDealPBN()
        table = dds.ddTableResults()
        myTable = ctypes.pointer(table)

        line = ctypes.create_string_buffer(80)

        # Need dealer
        tableDealPBN.cards = ("N:"+hand).encode('utf-8')

        res = dds.CalcDDtablePBN(tableDealPBN, myTable)

        if res != 1:
            error_message = dds.get_error_message(res)
            sys.stderr.write(f"Error Code: {res}, Error Message: {error_message}, Hand {hand.encode('utf-8')}\n")
            raise Exception(error_message)

        pres = dds.parResults()

        # vulnerable 
        # 0: None 1: Both 2: NS 3: EW 
        v = 0
        if vuln[0]: v = 2
        if vuln[1]: v = 3
        if vuln[0] and vuln[1]: v = 1

        res = dds.Par(myTable, pres, v)

        if res != 1:
            error_message = dds.get_error_message(res)
            sys.stderr.write(f"{Fore.RED}Error Code: {res}, Error Message: {error_message} {hand.encode('utf-8')}{Style.RESET_ALL}")
            return None

        par = ctypes.pointer(pres)

        if print_result:
            print("NS score: {}".format(par.contents.parScore[0].value.decode('utf-8')))
            print("EW score: {}".format(par.contents.parScore[1].value.decode('utf-8')))
            #print("NS list : {}".format(par.contents.parContractsString[0].value.decode('utf-8')))
            #print("EW list : {}\n".format(par.contents.parContractsString[1].value.decode('utf-8')))
        par = par.contents.parScore[0].value.decode('utf-8')
        ns_score = par.split()[1]
        return int(ns_score)
    
        
    # Solutions
    #1	Find the maximum number of tricks for the side to play.  Return only one of the optimum cards and its score.
    #2	Find the maximum number of tricks for the side to play.  Return all optimum cards and their scores.
    #3	Return all cards that can be legally played, with their scores in descending order.

    @staticmethod
    def _trick_number(hands_pbn, current_trick):
        """Derive trick number (1-13) from remaining cards in PBN hand."""
        pbn = hands_pbn[0]
        if ':' in pbn:
            pbn = pbn.split(':', 1)[1]
        remaining = sum(1 for c in pbn if c not in '. ')
        return (52 - remaining - len(current_trick)) // 4 + 1

    def solve(self, strain_i, leader_i, current_trick, hands_pbn, solutions):
        trick = self._trick_number(hands_pbn, current_trick)
        with ModelTimer.time_call(f'dds_solve_t{trick:02d}', items=len(hands_pbn)):
            results = self.solve_helper(strain_i, leader_i, current_trick, hands_pbn[:dds.MAXNOOFBOARDS], solutions)
            if len(hands_pbn) > dds.MAXNOOFBOARDS:
                i = dds.MAXNOOFBOARDS
                while i < len(hands_pbn):
                    more_results = self.solve_helper(strain_i, leader_i, current_trick, hands_pbn[i:i+dds.MAXNOOFBOARDS], solutions)

                    for card, values in more_results.items():
                        results[card] = results[card] + values

                    i += dds.MAXNOOFBOARDS

        return results 

    @staticmethod
    def _validate_pbn(pbn_str):
        """Validate PBN deal string: 4 hands, each with exactly 4 suits (3 dots)."""
        pbn = pbn_str
        if ':' in pbn:
            pbn = pbn.split(':', 1)[1]
        hands = pbn.strip().split(' ')
        if len(hands) != 4:
            return False
        for hand in hands:
            # Each hand must have exactly 3 dots (4 suits)
            if hand.count('.') != 3:
                return False
        return True

    def solve_helper(self, strain_i, leader_i, current_trick, hands_pbn, solutions):
        card_rank = [0x4000, 0x2000, 0x1000, 0x0800, 0x0400, 0x0200, 0x0100, 0x0080, 0x0040, 0x0020, 0x0010, 0x0008, 0x0004]

        # Filter out invalid PBN deals to prevent DDS segfault
        valid_hands = [h for h in hands_pbn if self._validate_pbn(h)]
        if not valid_hands:
            print(f"{Fore.RED}All {len(hands_pbn)} PBN deals invalid, skipping DDS solve{Style.RESET_ALL}")
            return {}
        if len(valid_hands) < len(hands_pbn):
            print(f"{Fore.YELLOW}Filtered {len(hands_pbn) - len(valid_hands)} invalid PBN deals{Style.RESET_ALL}")
        hands_pbn = valid_hands

        # Allocate per-call structures to avoid race conditions with concurrent API workers
        bo = dds.boardsPBN()
        solved = dds.solvedBoards()

        bo.noOfBoards = min(dds.MAXNOOFBOARDS, len(hands_pbn))

        for handno in range(bo.noOfBoards):
            bo.deals[handno].trump = (strain_i - 1) % 5
            bo.deals[handno].first = leader_i

            for i in range(3):
                bo.deals[handno].currentTrickSuit[i] = 0
                bo.deals[handno].currentTrickRank[i] = 0
                if i < len(current_trick):
                    bo.deals[handno].currentTrickSuit[i] = current_trick[i] // 13
                    bo.deals[handno].currentTrickRank[i] = 14 - current_trick[i] % 13

            bo.deals[handno].remainCards = hands_pbn[handno].encode('utf-8')

            bo.target[handno] = -1
            # Return all cards that can be legally played, with their scores in descending order.
            bo.solutions[handno] = solutions
            bo.mode[handno] = self.dds_mode

        # Run DDS in a forked child process to isolate segfaults from the server.
        # DDS's C library can crash on malformed input instead of returning an error.
        read_fd, write_fd = os.pipe()
        pid = os.fork()
        if pid == 0:
            # Child process — run DDS, write result, exit
            os.close(read_fd)
            try:
                res = dds.SolveAllBoards(ctypes.pointer(bo), ctypes.pointer(solved))
                if res != 1:
                    os.write(write_fd, pickle.dumps(('error', res, dds.get_error_message(res))))
                else:
                    # Extract results in child before sending
                    result_data = []
                    for handno in range(bo.noOfBoards):
                        fut = solved.solvedBoards[handno]
                        hand_cards = []
                        for i in range(fut.cards):
                            hand_cards.append((fut.suit[i], fut.rank[i], fut.score[i], fut.equals[i]))
                        result_data.append(hand_cards)
                    os.write(write_fd, pickle.dumps(('ok', result_data)))
            except Exception as e:
                os.write(write_fd, pickle.dumps(('exception', str(e))))
            finally:
                os.close(write_fd)
                os._exit(0)
        else:
            # Parent process — wait for child
            os.close(write_fd)
            data = b''
            while True:
                chunk = os.read(read_fd, 65536)
                if not chunk:
                    break
                data += chunk
            os.close(read_fd)
            _, status = os.waitpid(pid, 0)

            if not data or (os.WIFSIGNALED(status)):
                sig = os.WTERMSIG(status) if os.WIFSIGNALED(status) else -1
                print(f"{Fore.RED}DDS child crashed (signal {sig}), hands: {hands_pbn[0][:60]}...{Style.RESET_ALL}")
                return None

            msg = pickle.loads(data)
            if msg[0] == 'error':
                _, res, error_message = msg
                print(f"{Fore.RED}Error Code: {res}, Error Message: {error_message} {hands_pbn[0].encode('utf-8')} {current_trick} {leader_i}{Style.RESET_ALL}")
                return None
            elif msg[0] == 'exception':
                print(f"{Fore.RED}DDS exception: {msg[1]}{Style.RESET_ALL}")
                return None

            # Unpack results from child
            result_data = msg[1]

        if solutions == 1:
            # Just return the maximum number of the side to play for each sample
            card_results = {}
            card_results["max"] = []
            card_results["min"] = []
            for handno in range(bo.noOfBoards):
                hand_cards = result_data[handno]
                if not hand_cards:
                    continue
                suit_i, rank_i, score_i, _ = hand_cards[0]
                card_results["max"].append(score_i)
                _, _, last_score, _ = hand_cards[-1]
                card_results["min"].append(last_score)

        else:
            card_results = {}
            for handno in range(bo.noOfBoards):
                hand_cards = result_data[handno]
                for i, (suit_i, rank_i, score_i, eq_encoded) in enumerate(hand_cards):
                    card = suit_i * 13 + 14 - rank_i
                    if card not in card_results:
                        card_results[card] = []
                    card_results[card].append(score_i)
                    for k, rank_code in enumerate(card_rank):
                        if rank_code & eq_encoded > 0:
                            eq_card = suit_i * 13 + k
                            if eq_card not in card_results:
                                card_results[eq_card] = []
                            card_results[eq_card].append(score_i)

        return card_results


    def expected_tricks_dds(self, card_results):
        return {card:round((sum(values)/len(values)),2) for card, values in card_results.items()}

    def expected_tricks_dds_probability(self, card_results, probabilities_list : List[float]):
        # Convert to plain Python list to avoid numpy scalar overhead
        probs = [float(p) for p in probabilities_list]
        return {card: round(sum(p*res for p, res in zip(probs, result_list)),2) for card, result_list in card_results.items()}

    def p_made_target(self, tricks_needed):

        def fun(card_results):
            return {card:round(sum(1 for x in values if x >= tricks_needed)/len(values),3) for card, values in card_results.items()}
        return fun

    def print_dd_results(self, dd_solved, print_result=True, xcards=False):
        print("DD Result\n".join(
            f"{Card.from_code(int(k))}: [{', '.join(f'{x:>2}' for x in v[:20])}{' ]' if len(v) <= 20 else ' ...]'}"
            for k, v in dd_solved.items()
        ))

        # Create a new dictionary to store sorted counts for each key
        sorted_counts_dict = {}

        # Loop through the dictionary and process each key-value pair
        for key, array in dd_solved.items():
            # Use Counter to count the occurrences of each element
            element_count = Counter(array)
            
            # Sort the counts by frequency in descending order
            sorted_counts = sorted(element_count.items(), key=lambda x: x[1], reverse=True)
            
            # Store the sorted result in the new dictionary
            sorted_counts_dict[key] = sorted_counts

        # Print the sorted counts for each key
        for key, sorted_counts in sorted_counts_dict.items():
            print(f"Sorted counts for {Card.from_code(int(key), xcards)} DD:")
            for value, count in sorted_counts:
                print(f"  Tricks: {value}, Count: {count}")
