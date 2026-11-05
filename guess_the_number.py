import random


def play() -> None:
    print("Guess the Number!")
    print("I'm thinking of a number from 1 to 20. You have 5 guesses.")

    secret = random.randint(1, 20)
    for turn in range(1, 6):
        while True:
            try:
                guess = int(input(f"Guess {turn}/5: "))
            except ValueError:
                print("Please enter a whole number.")
                continue

            if 1 <= guess <= 20:
                break
            print("Pick a number from 1 to 20.")

        if guess == secret:
            print(f"You got it in {turn} {'guess' if turn == 1 else 'guesses'}! You win!")
            return
        if guess < secret:
            print("Too low!")
        else:
            print("Too high!")

    print(f"Out of guesses! The number was {secret}.")


if __name__ == "__main__":
    play()
