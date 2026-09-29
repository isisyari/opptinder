# AI class september 8th notes
# inheritance: create a parent class and child class that inherits those abilitis: useful for object oriented programming
#uml diagram when u explain inheritance 
class Frontier:
    #parent class for queues,stacks and priority queues
    def __init__(self): #constructor
        self.frontier=[] #initialize frontier
    def pop(self):
        #pop from priority queue n stack
        return self.frontier.pop(0)  # remove from the front (index 0)
    
    def is_empty(self):
        #delete in pripority queue
        return len(self.frontier)==0

class Stack(Frontier): #class to define a stack 

    def add(self,state):
        #add to stack
        self.frontier.insert(0,state) #add at the end

class Queue(Frontier):  # class to define a QUEUE (FIFO - First In First Out)

    def add(self, state):
        # add to the END of the queue
        # when we pop from front (index 0), oldest item comes out first = FIFO!
        self.frontier.append(state)  # append adds to the END

class PriorityQueue(Frontier):
    #for this we need to enforce tuples

    def add(self,state):
        #add to priority queue
        if type(state)!= tuple:
            raise TypeError("Priority queue requires a tuple")
        if(type(state[0])!=int and type(state[0])!=float):
            raise TypeError("first element of tuple must be a number")
        self.frontier.append(state) #add at the end


# challenge PARENT CLASS FOR SEARCH ALGORITHMS

# from clas notes:  search algorithms follow the same basic pattern:
#   1. Start at the initial state
#   2. Check if we're at the goal
#   3. If not, expand (look at neighbors)
#   4. Add neighbors to frontier
#   5. Pick t next state from frontier
#   6. Repeat until goal found or frontier empty
#
# !!! difference between algorithms is ? they pick the next state

class SearchAlgorithm:
    """
    Parent class for all search algorithms.
    """

    def __init__(self, frontier):
        """
        Constructor - sets up the search algorithm.
        note from class: 
                     - Stack for DFS
                     - Queue for BFS
                     - PriorityQueue for UCS and Greedy
        """
        self.frontier = frontier      # store states to explore
        self.explored = set()         # states we've  visited set bc optimized for checking inclusion 

    def search(self, initial_state, goal_state, get_neighbors):
        """
        find a path from initial to goal.
        """
        # Step 1: Add the starting state to our frontier / store where we at
        self.frontier.add((initial_state, [initial_state]))

        # Step 2: Keep searching while there are states to explore
        while not self.frontier.is_empty():

            # Step 3: Get the next state to explore
            current_state, path = self.frontier.pop()

            # Step 4: Check if we found the goal
            if current_state == goal_state:
                return path  # Return the path we took

            # Step 5: Skip if we've already been here
            if current_state in self.explored:
                continue

            # Step 6: Mark this state as explored
            self.explored.add(current_state)

            # Step 7: Look at all neighbors and add unexplored ones
            for neighbor in get_neighbors(current_state):
                if neighbor not in self.explored:
                    new_path = path + [neighbor]  # extend the path
                    self.frontier.add((neighbor, new_path))

        # If we get here, no path is possible
        return None

class UCS:
    "class for ucs"
    def __init__(self):
        self.explored=set()
        self.frontier=f.PriorityQueue()
    def start(self,start):
        self.frontier.add(0,start,[start]) #add cost, state and path

    def search(self):
        explored={}
        while frontier is not empty:
            (g,state,path)=frontier.pop_min()
            if state is the goal: return path,g
            if state in explored: continue
            explored.add(state)
            for(nbr,w) in neighbors(state):
                if nbr not in explored:
                    push(g+w,nbr,path+[nbr])
        return None