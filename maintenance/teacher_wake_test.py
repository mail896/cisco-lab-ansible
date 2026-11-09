"""Retired Teacher suspend entry point."""
import argparse
import sys

REASON = ('Standby-Test auf LAB-Teacher dauerhaft gesperrt. '
          'Die Teacher-Steuerung verwendet externes Wake-on-LAN.')


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
