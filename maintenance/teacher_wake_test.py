"""Retired: LAB-Teacher required a hard restart after its suspend/wake trial."""
import argparse
import sys

REASON = ('Standby-Test auf LAB-Teacher dauerhaft gesperrt: Der Test vom 07.11.2026 '
          'erforderte einen Neustart vor Ort. WOL aus vollständigem Ausschalten '
          'ist laut Betreiber bewährt. Keine weiteren Suspend-Versuche.')


def cycle():
    raise RuntimeError(REASON)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suspend-test', action='store_true', help='Retired; always rejected')
    parser.parse_args()
    print(REASON, file=sys.stderr)
    raise SystemExit(1)


if __name__ == '__main__':
    main()
