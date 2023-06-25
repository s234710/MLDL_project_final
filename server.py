import copy
from collections import OrderedDict

import pandas as pd

import numpy as np
import torch
from torch import nn
from FedSR_pers import featurize


class Server:
    def __init__(self, args, train_clients, test_clients, model, metrics):
        self.args = args
        self.train_clients = train_clients
        self.test_clients = test_clients
        self.model = model
        self.metrics = metrics
        self.model_params_dict = copy.deepcopy(self.model.state_dict())
        self.prob = {
            'clients_per_round': 10,
            'prob_10_clients': 0.5,
            'prob_30_clients': 0.0001
        }
        self.clients_loss= {}
        for c in train_clients:
          self.clients_loss[c] = 4
        self.device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
        

    def select_clients(self):
        num_clients = min(self.args.clients_per_round, len(self.train_clients))
        return np.random.choice(self.train_clients, num_clients, replace=False)
    
    def generate_probs(self, prob):
        num_clients = len(self.train_clients)
        client_probs = np.ones(len(self.train_clients))

        if prob == 10:# Set probability for 10% of clients
            num_10_clients = int(0.1 * num_clients)
            client_probs[:num_10_clients] = self.prob['prob_10_clients']/num_10_clients
            client_probs[num_10_clients:] = (1-self.prob['prob_10_clients'])/(num_clients - num_10_clients)
        elif prob==30:
            # Set probability for 30% of clients
            num_30_clients = int(0.3 * num_clients)
            client_probs[:num_30_clients] = self.prob['prob_30_clients']/num_30_clients
            client_probs[num_30_clients:] = (1-self.prob['prob_30_clients'])/(num_clients - num_30_clients)


        # Normalize probabilities
        client_probs /= np.sum(client_probs)

        self.client_probs=client_probs

    def get_clients_with_highest_losses(self, d):
        sorted_clients = sorted(self.clients_loss.items(), key=lambda x: x[1], reverse=True)
        top_clients = sorted_clients[:d]
        return top_clients

    def smart_select_clients(self):
        #tra tutti i clients(inizializzati tutti con loss=4(valore arbitrario alto)
        # ne seleziona num_clients_per_round con la losso più alta

        num_clients = min(self.args.clients_per_round, len(self.train_clients))
        #selected_clients = np.random.choice(self.train_clients, num_clients, replace=False, p=self.client_probs)
        top_clients = self.get_clients_with_highest_losses(num_clients)
        top_clients= [client[0] for client in top_clients]

        return top_clients

    def train_round(self, clients):
        """
        This method trains the model with the dataset of the clients. It handles the training at single round level.
        :param clients: list of all the clients to train
        :return: model updates gathered from the clients, to be aggregated
        """
        updates = []
        server_state_dict = copy.deepcopy(self.model.state_dict())
        for  i,c in enumerate(clients):
            
            # train the client 
            
            #c.model.load_state_dict(self.model_params_dict)
            #MODIFICARE PER RICEVERE PARAMETRI DEL MODELLO ESTESO DI FEDSR
            c.model.load_state_dict(server_state_dict)
            # client fa il train uguale in goni caso e trona la loss dell'ultima epoch
            # se è poc salva la loss nel dizionario (spostato in self), sennò non fa nulla del valore ritornato e continua
            if self.args.POC:
                loss = c.train()
                self.clients_loss[c] = abs(loss)
            else:
                c.train()

            # Get the updated model's parameters
            #if self.args.fedSR:
            #    updated_params = c.net.state_dict()
            #else:
            #    updated_params = c.model.state_dict()
            updated_params=c.model.state_dict()

            # Compute the difference between the current and updated parameters
            # senza '- self.model_params_dict[key] ' l'accuracy diminuisce ad ogni round invece di aumentare
            updates.append(OrderedDict({key: updated_params[key] - self.model_params_dict[key] for key in updated_params}))
            
            # libera la memoria dalla copia del modello fatta dal client
            

        return updates

    def aggregate(self, updates, clients):
    
        aggregated_params = OrderedDict()
        total_samples = sum(c.get_len() for c in clients)
       
        for key in updates[0].keys():
        # Sum the weighted updates for each parameter
            param_sum = sum([updates[i][key] * clients[i].get_len() / total_samples for i in range(len(clients))])
        # Apply the weighted average update to the server's model parameters
            aggregated_params[key] = self.model_params_dict[key] + param_sum

        # da mettere un if: se c'è FedSR l'aggregation è da fare anche su cls non solo sul modello
        # oppure direttamente una volta creto il layer in piu si passa solo quel modello e ogni volta il client nella parte fedsr 
        # tira fuori l'ultimo layer per fare la domain generalization

        return aggregated_params
    def aggregate_SR(self, updates, clients):
    
        aggregated_params = OrderedDict()
        total_samples = sum(c.get_len() for c in clients)
       
        for key in updates[0].keys():
        # Sum the weighted updates for each parameter
            param_sum = sum([updates[i][key] * clients[i].get_len() / total_samples for i in range(len(clients))])
        # Apply the weighted average update to the server's model parameters
            aggregated_params[key] = self.model_params_dict[key] + param_sum

        return aggregated_params


    def train(self):
        """
        This method orchestrates the training, evaluations, and tests at the round level.
        """
        #creo variabili per loss e accuracy per grafici
        df_loss = pd.DataFrame(columns=['x_round','y'])
        df_accuracy = pd.DataFrame(columns=['x_round', 'y'])

        for r in range(self.args.num_rounds):
            # Select clients for this round
            if self.args.POC:
                clients = self.smart_select_clients()
            else :
                clients= self.select_clients()

            # Train clients and gather updates
            updates = self.train_round(clients)

            # Aggregate the updates
            #if self.args.fedSR:
            #   aggregated_params = self.aggregate_SR(updates, clients)
            #else:
            #    aggregated_params = self.aggregate(updates, clients)
            aggregated_params=self.aggregate(updates,clients)
            # Update the server's model parameters
            self.model.load_state_dict(aggregated_params)

            # Evaluate on train clients
            train_loss, train_accuracy = self.eval_train()
            train_accuracy = train_accuracy * 100
            print(f"Round {r + 1}: Train Loss: {train_loss:.4f}, Train Accuracy: {train_accuracy:.4f}")

            # salvo dati del round
            df_loss.loc[len(df_loss)] = [r+1, train_loss]
            df_accuracy.loc[len(df_accuracy)] = [r+1, train_accuracy]

            # Test on test clients
            # attualmente self.test non ritorna nulla/ non fa nulla
            #test_loss, test_accuracy = self.test()
            #print(f"Round {r + 1}: Test Loss: {test_loss:.4f}, Test Accuracy: {test_accuracy:.4f}")
            
        # salvataggio file dati
        df_loss.to_csv('loss_1epoch_5clientPerRound_25r_IID.csv', index=False)
        df_accuracy.to_csv('accuracy_1epoch_5clientPerRound_25r_IID.csv', index=False)

    def eval_train(self):
        """This method handles the evaluation on the train clients. """
        total_loss = 0.0
        total_correct = 0
        total_samples = 0

        with torch.no_grad():
            # Set the model in evaluation mode
            if self.args.fedSR:
              self.cls=self.model[-1] # ho aggiunto cls dal modello grande al posto di passare due elementi ogni volta
              self.cls.to(self.device)
              self.net=nn.Sequential(*self.model[:-1])
              self.net.to(self.device)
            self.model.eval()

            for client in self.train_clients:
                client_samples = 0
                client_correct = 0
                client_loss = 0.0

                '''for _, (data, target) in enumerate(client.train_loader):
                    data, target = data.to(self.args.device), target.to(self.args.device)
                    output = self.model(data)
                    loss = torch.nn.functional.cross_entropy(output, target)
                    predictions = output.argmax(dim=1)
                    correct = (predictions == target).sum().item()

                    client_loss += loss.item() * data.size(0)
                    client_correct += correct
                    client_samples += data.size(0)'''

                for _, (data, target) in enumerate(client.train_loader):
                    inputs = data
                    labels = target
                    inputs = inputs.cuda()
                    labels = labels.cuda()
                    
                    if self.args.fedSR:
                      z, (z_mu, z_sigma) = featurize(self.net,inputs,self.args.z_dim) #passare tutto il modello a featurize, non solo il net
                      preds = torch.softmax(self.cls(z),dim=1)
                      preds = preds.view([self.num_samples,-1,self.num_classes]).mean(0)
                      outputs=torch.log(preds) #ho corretto il problema potenziale del FedSR

                      #outputs = self.cls(z)
                    else:
                      outputs = self.model(inputs)

                    loss = torch.nn.functional.cross_entropy(outputs, labels)
                    
                    client_loss += loss.item()
                    _, prediction = torch.max(outputs.data, 1)
                    client_samples += labels.size(0)
                    client_correct += (prediction == labels).sum().item()

                # Accumulate results for each client
                total_loss += client_loss
                total_correct += client_correct
                total_samples += client_samples

        # Calculate average metrics across all clients
        avg_loss = total_loss / total_samples
        accuracy = total_correct / total_samples

        return avg_loss, accuracy
    
    def test(self):
        """
        This method handles the test on the test clients
        """
        for client in self.test_clients:
            metric = self.metrics  
            client.model.load_state_dict(self.model_params_dict)
            client.test(metric)  # Esegue il test utilizzando il metodo test del client

            # Stampa i risultati della metrica per il client corrente
            print(f"Testing  client {client.name}")
        # metric e dict, dict non ha funzione get results
        # print(f"Accuracy: {metric.get_results()}")


'''def test(self):
    """
    This method handles the test on the test clients.
    """
    total_loss = 0.0
    total_correct = 0
    total_samples = 0

    with torch.no_grad():
        # Set the model in evaluation mode
        self.model.eval()

        for client in self.test_clients:
            client_samples = 0
            client_correct = 0
            client_loss = 0.0

            for _, (data, target) in enumerate(client.test_loader):
                inputs = data
                labels = target
                inputs = inputs.cuda()
                labels = labels.cuda()
                outputs = self.model(inputs)

                loss = torch.nn.functional.cross_entropy(outputs, labels)
                
                client_loss += loss.item()
                _, prediction = torch.max(outputs.data, 1)
                client_samples += labels.size(0)
                client_correct += (prediction == labels).sum().item()
            
            total_loss += client_loss
            total_correct += client_correct
            total_samples += client_samples
            

    # Calculate average metrics across all clients
    avg_loss = total_loss / total_samples
    accuracy = total_correct / total_samples

    return avg_loss, accuracy'''
'''import torch

def test(self, metric):
    """
    This method tests the model on the local dataset of the client.
    :param metric: StreamMetric object
    """
    self.model.eval()  # Imposta il modello in modalità di valutazione (non addestramento)
    with torch.no_grad():
        for i, (images, labels) in enumerate(self.test_loader):
            #images = images.to(self.device)
            #labels = labels.to(self.device)
            
            outputs = self.model(images)  # Esegue l'inferenza sulle immagini
            self.update_metric(metric, outputs, labels)  # Aggiorna la metrica
            
    #self.model.train()  # Riporta il modello in modalità di addestramento'''

    