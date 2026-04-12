"""
Subprocess wrapper for DDS SolveAllBoards.
Reads JSON from stdin, calls DDS, writes JSON to stdout.
If DDS crashes, the subprocess dies without affecting the parent.
"""
import sys
import json
import ctypes

# Bootstrap path so dds module can be found
import os
script_dir = os.path.dirname(os.path.abspath(__file__))
src_dir = os.path.dirname(script_dir)
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

from ddsolver import dds


def main():
    data = json.loads(sys.stdin.read())

    bo = dds.boardsPBN()
    solved = dds.solvedBoards()

    bo.noOfBoards = data['noOfBoards']
    card_rank = [0x4000, 0x2000, 0x1000, 0x0800, 0x0400, 0x0200, 0x0100, 0x0080, 0x0040, 0x0020, 0x0010, 0x0008, 0x0004]

    for handno in range(bo.noOfBoards):
        deal = data['deals'][handno]
        bo.deals[handno].trump = deal['trump']
        bo.deals[handno].first = deal['first']
        for i in range(3):
            bo.deals[handno].currentTrickSuit[i] = deal['currentTrickSuit'][i]
            bo.deals[handno].currentTrickRank[i] = deal['currentTrickRank'][i]
        bo.deals[handno].remainCards = deal['remainCards'].encode('utf-8')
        bo.target[handno] = deal['target']
        bo.solutions[handno] = deal['solutions']
        bo.mode[handno] = deal['mode']

    res = dds.SolveAllBoards(ctypes.pointer(bo), ctypes.pointer(solved))
    if res != 1:
        error_message = dds.get_error_message(res)
        json.dump({'error': True, 'code': res, 'message': error_message}, sys.stdout)
        return

    solutions = data['deals'][0]['solutions']
    if solutions == 1:
        result = {'type': 'solutions1', 'max': [], 'min': []}
        for handno in range(bo.noOfBoards):
            fut = solved.solvedBoards[handno]
            result['max'].append(fut.score[0])
            result['min'].append(fut.score[fut.cards - 1])
    else:
        result = {'type': 'full', 'boards': []}
        for handno in range(bo.noOfBoards):
            fut = solved.solvedBoards[handno]
            cards = []
            for i in range(fut.cards):
                cards.append({
                    'suit': fut.suit[i],
                    'rank': fut.rank[i],
                    'score': fut.score[i],
                    'equals': fut.equals[i]
                })
            result['boards'].append(cards)

    json.dump(result, sys.stdout)


if __name__ == '__main__':
    main()
