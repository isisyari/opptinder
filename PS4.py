from string import ascii_uppercase
from random import random, choices, choice

def gen8():
    """Returns a random 8-letter string"""
    return ''.join(choices(ascii_uppercase, k=8))

def mix8(string1, string2) -> str:
    """Returns a mixture of the two strings"""

    result = []

    for i in range(8): #note to self: 8 is from the 8 letter string this is making the function act like a coinflip to decide which letter to take from each parent(string) to give the child(string) 
        if random() < 0.05: #otherwise it would lose like too many of its good letters
 #explai: solution idea bc we keep getting not to the right word bc the good strings become identical to each other and we need to add some randomness to the mix so that we can get new letters in the mix and not just the same letters over and over again
            result.append(choice(ascii_uppercase))
        elif random() < 0.5: #this is 0.5 aand the other is 0.05 so this is more likely to happen than the other one
            result.append(string1[i])
        else:
            result.append(string2[i])

    return ''.join(result)

def fitness(string) -> int: #Note to self: fitness function is a way to measure how close a string is to the target string "METHINKS" and this line shows how it returns an integer
    
    score = 0
    for letter1, letter2 in zip(string, "METHINKS"): #zip is new for me so note: pairs up the two sequences from above item by item, so like in pairs
        if letter1==letter2:
            score += 1 #if letters match the METHINKS add 1 to score 

    return score

start = [gen8() for i in range(10)] #generates 10 random strings

def one_generation(start:list[str]) -> list[str]: 
    scores = [(fitness(word), word) for word in start] #word is a loop item like i so is not defined score,string, list creation per word in start
    scores.sort() 
    starting_pop = [word for _, word in scores[7:]]
    population= starting_pop.copy()
    population.append(mix8(starting_pop[0],starting_pop[1]))
    population.append(mix8(starting_pop[0],starting_pop[2]))
    population.append(mix8(starting_pop[1],starting_pop[2]))
    
    for i in range(4):
        population.append(gen8()) #is comparing w 4 random generated strings to see if they are better than the current population and if so they will be added to the population
    
    return population
pop=start
for i in range(1000):
    pop=one_generation(pop)

for w in sorted(pop, key=fitness, reverse=True): #reverse bc is usually smallest to largest
    print(fitness(w), w)



    
    





